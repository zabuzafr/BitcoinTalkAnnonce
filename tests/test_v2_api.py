"""v2 endpoints: i18n catalog, stats, timeseries, by_algorithm, list, scan, analyze."""

import dashboard
import conftest
from fastapi.testclient import TestClient

DB = "/tmp/test_v2_api.db"


def _client(db_path: str = DB) -> TestClient:
    original = dashboard.DB_PATH
    dashboard.DB_PATH = db_path
    return TestClient(dashboard.app)


def _restore(original: str) -> None:
    dashboard.DB_PATH = original


def _seed(db_path: str, projects) -> None:
    analyzer = conftest.make_analyzer(db_path)
    for p in projects:
        if isinstance(p, (tuple, list)):
            tid, score = p[0], p[1]
        else:
            tid, score = p.topic_id, (p.final_score if p.final_score is not None else 60)
        conftest.insert_project(analyzer, tid, score)


def _fresh():
    import os
    for ext in ("", "-wal", "-shm"):
        try:
            os.unlink(DB + ext)
        except FileNotFoundError:
            pass


def _reset_state():
    dashboard._reanalyzing.clear()
    dashboard._scan_state.update(
        status="idle", scan_id=None, pages_done=0, pages_total=0,
        started_at=None, finished_at=None, message=None)


class TestI18nEndpoints:
    def test_languages_lists_both(self):
        original = dashboard.DB_PATH
        client = _client()
        try:
            r = client.get("/api/i18n/languages")
            assert r.status_code == 200
            langs = {item["code"] for item in r.json()}
            assert {"fr", "en"} <= langs
        finally:
            _restore(original)

    def test_catalog_fr(self):
        original = dashboard.DB_PATH
        client = _client()
        try:
            r = client.get("/api/i18n/fr")
            assert r.status_code == 200
            payload = r.json()
            assert payload["title"]
            assert payload["dashboard"]["stat_total"]
        finally:
            _restore(original)

    def test_catalog_en(self):
        original = dashboard.DB_PATH
        client = _client()
        try:
            r = client.get("/api/i18n/en")
            assert r.status_code == 200
            assert r.json()["dashboard"]["stat_total"]
        finally:
            _restore(original)


class TestStats:
    def test_stats_absent_db(self, tmp_path):
        db_file = str(tmp_path / "absent.db")
        original = dashboard.DB_PATH
        dashboard.DB_PATH = db_file
        try:
            client = TestClient(dashboard.app)
            r = client.get("/api/stats")
        finally:
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["db_exists"] is False
        assert body["total_projects"] == 0
        assert body["avg_final_score"] is None

    def test_stats_populated(self, tmp_path):
        db_file = str(tmp_path / "stats.db")
        _fresh()
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [
                (101, 90), (102, 40), (103, 70), (104, 55),
            ])
            client = TestClient(dashboard.app)
            r = client.get("/api/stats")
        finally:
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["db_exists"] is True
        assert body["total_projects"] == 4
        assert body["active_count"] == 4
        assert body["avg_final_score"] == 63.8
        assert body["max_final_score"] == 90
        assert body["min_final_score"] == 40
        assert body["fork_count"] == 0
        assert body["last_analysis"]


class TestTimeseries:
    def _db(self, tmp_path):
        return str(tmp_path / "ts.db")

    def test_week(self, tmp_path):
        db_file = self._db(tmp_path)
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(201, 70), (202, 80), (203, 60)])
            client = TestClient(dashboard.app)
            r = client.get("/api/stats/timeseries", params={"period": "week"})
        finally:
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["period"] == "week"
        assert len(body["buckets"]) == 12
        assert sum(b["count"] for b in body["buckets"]) == 3

    def test_month(self, tmp_path):
        db_file = self._db(tmp_path)
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(301, 70)])
            client = TestClient(dashboard.app)
            r = client.get("/api/stats/timeseries", params={"period": "month"})
        finally:
            _restore(original)
        body = r.json()
        assert body["period"] == "month"
        assert len(body["buckets"]) == 12

    def test_year(self, tmp_path):
        db_file = self._db(tmp_path)
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(302, 65)])
            client = TestClient(dashboard.app)
            r = client.get("/api/stats/timeseries", params={"period": "year"})
        finally:
            _restore(original)
        assert r.json()["period"] == "year"

    def test_default_period_week(self, tmp_path):
        db_file = self._db(tmp_path)
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            client = TestClient(dashboard.app)
            r = client.get("/api/stats/timeseries")
        finally:
            _restore(original)
        assert r.status_code == 200
        assert r.json()["period"] == "week"

    def test_include_inactive_param(self, tmp_path):
        db_file = self._db(tmp_path)
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(303, 50)])
            client = TestClient(dashboard.app)
            r = client.get("/api/stats/timeseries", params={"include_inactive": 1})
        finally:
            _restore(original)
        assert r.status_code == 200
        assert sum(b["count"] for b in r.json()["buckets"]) == 1


class TestByAlgorithm:
    def _seed_algos(self, db_file):
        import sqlite3
        _seed(db_file, [(401, 80), (402, 75), (403, 60)])
        conn = sqlite3.connect(db_file)
        conn.execute("UPDATE projects SET mining_algorithm='sha256' WHERE topic_id IN (401, 402)")
        conn.execute("UPDATE projects SET mining_algorithm='scrypt' WHERE topic_id = 403")
        conn.commit()
        conn.close()

    def test_default(self, tmp_path):
        db_file = str(tmp_path / "algo.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            self._seed_algos(db_file)
            client = TestClient(dashboard.app)
            r = client.get("/api/stats/by_algorithm")
        finally:
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        names = [a["algorithm"] for a in body["algorithms"]]
        assert "sha256" in names and "scrypt" in names
        sha = next(a for a in body["algorithms"] if a["algorithm"] == "sha256")
        assert sha["count"] == 2

    def test_top_limit(self, tmp_path):
        db_file = str(tmp_path / "algo2.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            self._seed_algos(db_file)
            client = TestClient(dashboard.app)
            r = client.get("/api/stats/by_algorithm", params={"top": 1})
        finally:
            _restore(original)
        assert len(r.json()["algorithms"]) == 1

    def test_include_inactive(self, tmp_path):
        db_file = str(tmp_path / "algo3.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            self._seed_algos(db_file)
            client = TestClient(dashboard.app)
            r = client.get("/api/stats/by_algorithm", params={"include_inactive": 1})
        finally:
            _restore(original)
        assert r.status_code == 200
        assert r.json()["total"] == 3

    def test_invalid_include_inactive(self, tmp_path):
        db_file = str(tmp_path / "algo4.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            client = TestClient(dashboard.app)
            r = client.get("/api/stats/by_algorithm", params={"include_inactive": 5})
        finally:
            _restore(original)
        assert r.status_code == 422

    def test_absent_db(self, tmp_path):
        db_file = str(tmp_path / "absent_algo.db")
        original = dashboard.DB_PATH
        dashboard.DB_PATH = db_file
        try:
            client = TestClient(dashboard.app)
            r = client.get("/api/stats/by_algorithm")
        finally:
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0
        assert body["algorithms"] == []


class TestProjects:
    def test_list_default(self, tmp_path):
        db_file = str(tmp_path / "list.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(501, 85), (502, 60)])
            client = TestClient(dashboard.app)
            r = client.get("/api/projects")
        finally:
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        assert body[0]["topic_id"] == 501

    def test_list_search(self, tmp_path):
        db_file = str(tmp_path / "list2.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(503, 70), (504, 55)])
            import sqlite3
            conn = sqlite3.connect(db_file)
            conn.execute("UPDATE projects SET title = 'Zcash style coin' WHERE topic_id = 503")
            conn.commit()
            conn.close()
            client = TestClient(dashboard.app)
            r = client.get("/api/projects", params={"q": "Zcash"})
        finally:
            _restore(original)
        body = r.json()
        assert len(body) == 1
        assert body[0]["topic_id"] == 503

    def test_list_absent_db(self, tmp_path):
        db_file = str(tmp_path / "absent_list.db")
        original = dashboard.DB_PATH
        dashboard.DB_PATH = db_file
        try:
            client = TestClient(dashboard.app)
            r = client.get("/api/projects")
        finally:
            _restore(original)
        assert r.status_code == 200
        assert r.json() == []

    def test_sort_final_score_desc(self, tmp_path):
        db_file = str(tmp_path / "sort.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(601, 50), (602, 95), (603, 70)])
            client = TestClient(dashboard.app)
            r = client.get("/api/projects")
        finally:
            _restore(original)
        scores = [p["final_score"] for p in r.json()]
        assert scores == sorted(scores, reverse=True)


class TestProjectDetail:
    def test_detail(self, tmp_path):
        db_file = str(tmp_path / "detail.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(701, 80)])
            client = TestClient(dashboard.app)
            r = client.get("/api/projects/701")
        finally:
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["topic_id"] == 701
        assert "red_flags" in body
        assert "unique_features" in body

    def test_detail_404(self, tmp_path):
        db_file = str(tmp_path / "detail404.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(702, 80)])
            client = TestClient(dashboard.app)
            r = client.get("/api/projects/9999")
        finally:
            _restore(original)
        assert r.status_code == 404


class TestHistory:
    def test_history(self, tmp_path):
        db_file = str(tmp_path / "hist.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(801, 80)])
            client = TestClient(dashboard.app)
            r = client.get("/api/history/801")
        finally:
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["topic_id"] == 801
        assert len(body["history"]) >= 1

    def test_history_404(self, tmp_path):
        db_file = str(tmp_path / "hist404.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(802, 80)])
            client = TestClient(dashboard.app)
            r = client.get("/api/history/424242")
        finally:
            _restore(original)
        assert r.status_code == 404


class TestDisableEnable:
    def test_disable(self, tmp_path):
        db_file = str(tmp_path / "dis.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(901, 80)])
            client = TestClient(dashboard.app)
            r = client.post("/api/projects/901/disable")
        finally:
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] is not None
        assert body["status"] == "disabled"

    def test_enable(self, tmp_path):
        db_file = str(tmp_path / "en.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(902, 80)])
            client = TestClient(dashboard.app)
            r = client.post("/api/projects/902/enable")
        finally:
            _restore(original)
        assert r.status_code == 200
        assert r.json()["status"] == "enabled"

    def test_disable_404(self, tmp_path):
        db_file = str(tmp_path / "dis404.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(903, 80)])
            client = TestClient(dashboard.app)
            r = client.post("/api/projects/77777/disable")
        finally:
            _restore(original)
        assert r.status_code == 404

    def test_enable_404(self, tmp_path):
        db_file = str(tmp_path / "en404.db")
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(904, 80)])
            client = TestClient(dashboard.app)
            r = client.post("/api/projects/77777/enable")
        finally:
            _restore(original)
        assert r.status_code == 404


class TestAnalyze:
    def test_analyze_no_ollama(self, tmp_path):
        db_file = str(tmp_path / "ana_no.db")
        original_avail, original_do = dashboard._ollama_available, dashboard._do_reanalyze
        original_state = {
            k: dashboard._scan_state[k] for k in dashboard._scan_state
        }
        _rean = dashboard._reanalyzing
        try:
            dashboard._ollama_available = lambda: False
            dashboard.DB_PATH = db_file
            _seed(db_file, [(1101, 80)])
            client = TestClient(dashboard.app)
            r = client.post("/api/projects/1101/analyze")
        finally:
            dashboard._ollama_available, dashboard._do_reanalyze = original_avail, original_do
            dashboard.DB_PATH = original_avail and dashboard.DB_PATH
            _restore(dashboard.DB_PATH)
        assert r.status_code == 503

    def test_analyze_unknown_topic(self, tmp_path, monkeypatch):
        db_file = str(tmp_path / "ana_unk.db")
        monkeypatch.setattr(dashboard, "_ollama_available", lambda: True)
        called = []
        async def fake_do(topic_id):
            called.append(topic_id)
        monkeypatch.setattr(dashboard, "_do_reanalyze", fake_do)
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(1102, 80)])
            client = TestClient(dashboard.app)
            r = client.post("/api/projects/31337/analyze")
        finally:
            _restore(original)
        assert r.status_code == 404
        assert called == []

    def test_analyze_conflict(self, tmp_path, monkeypatch):
        db_file = str(tmp_path / "ana_conf.db")
        monkeypatch.setattr(dashboard, "_ollama_available", lambda: True)
        async def fake_do(topic_id):
            pass
        monkeypatch.setattr(dashboard, "_do_reanalyze", fake_do)
        original = dashboard.DB_PATH
        _reanalyzing = dashboard._reanalyzing
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(1103, 80)])
            _reanalyzing.add(1103)
            client = TestClient(dashboard.app)
            r = client.post("/api/projects/1103/analyze")
        finally:
            _reanalyzing.discard(1103)
            _restore(original)
        assert r.status_code == 409

    def test_analyze_queued(self, tmp_path, monkeypatch):
        db_file = str(tmp_path / "ana_ok.db")
        monkeypatch.setattr(dashboard, "_ollama_available", lambda: True)
        called = []

        async def fake_do(topic_id):
            called.append(topic_id)
            dashboard._reanalyzing.discard(topic_id)
        monkeypatch.setattr(dashboard, "_do_reanalyze", fake_do)
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(1104, 70)])
            client = TestClient(dashboard.app)
            r = client.post("/api/projects/1104/analyze")
        finally:
            dashboard._reanalyzing.discard(1104)
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["topic_id"] == 1104
        assert body["status"] == "queued"


class TestScan:
    def test_start(self, tmp_path, monkeypatch):
        async def fake_scan(scan_id, section_id, pages):
            dashboard._scan_state.update(
                status="completed", pages_done=2, pages_total=2,
                finished_at="now", message="ok")
        monkeypatch.setattr(dashboard, "_run_scan", fake_scan)
        original = dashboard.DB_PATH
        dashboard.DB_PATH = str(tmp_path / "scan.db")
        try:
            client = TestClient(dashboard.app)
            r = client.post("/api/scan", json={"section_id": 40, "pages": 2})
        finally:
            _reset_state()
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["scan_id"]
        assert body["status"] == "running"

    def test_status_while_running(self, tmp_path, monkeypatch):
        async def fake_scan(scan_id, section_id, pages):
            await _asyncio.sleep(0.2)
        monkeypatch.setattr(dashboard, "_run_scan", fake_scan)
        original = dashboard.DB_PATH
        dashboard.DB_PATH = str(tmp_path / "scan2.db")
        try:
            client = TestClient(dashboard.app)
            client.post("/api/scan", json={"section_id": 40, "pages": 1})
            r = client.get("/api/scan/status")
        finally:
            _reset_state()
            _restore(original)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("running", "completed", "failed")
        assert body["pages_total"] >= 1

    def test_status_idle(self):
        _reset_state()
        original = dashboard.DB_PATH
        client = TestClient(dashboard.app)
        try:
            r = client.get("/api/scan/status")
        finally:
            _restore(original)
        assert r.status_code == 200
        assert r.json()["status"] == "idle"
