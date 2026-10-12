"""v2 endpoints: i18n catalog, stats, timeseries, by_algorithm, list, scan, analyze."""

import asyncio
import json
import re
import sqlite3
from pathlib import Path

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
    def test_analyze_llm_unavailable(self, tmp_path, monkeypatch):
        db_file = str(tmp_path / "ana_no.db")
        monkeypatch.setattr(dashboard, "_llm_available", lambda: (False, "injoignable"))
        original = dashboard.DB_PATH
        try:
            dashboard.DB_PATH = db_file
            _seed(db_file, [(1101, 80)])
            client = TestClient(dashboard.app)
            r = client.post("/api/projects/1101/analyze")
        finally:
            _restore(original)
        assert r.status_code == 503
        assert r.json()["detail"] == "llm_unavailable"
        assert r.json()["reason"] == "injoignable"

    def test_analyze_unknown_topic(self, tmp_path, monkeypatch):
        db_file = str(tmp_path / "ana_unk.db")
        monkeypatch.setattr(dashboard, "_llm_available", lambda: (True, None))
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
        monkeypatch.setattr(dashboard, "_llm_available", lambda: (True, None))
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
        monkeypatch.setattr(dashboard, "_llm_available", lambda: (True, None))
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
        assert r.status_code == 202
        body = r.json()
        assert body["scan_id"]
        assert body["status"] == "running"

    def test_status_while_running(self, tmp_path, monkeypatch):
        async def fake_scan(scan_id, section_id, pages):
            await asyncio.sleep(0.2)
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
        assert body["status"] == "running"
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


class TestStaticAssets:
    def test_index_references_static_bundle(self):
        client = _client()
        r = client.get("/")
        assert r.status_code == 200
        body = r.text
        assert "/static/app.css" in body
        assert "/static/app.js" in body
        assert "__DEFAULT_LANG__" not in body
        assert "__DEFAULT_SECTION__" not in body

    def test_index_embeds_default_section(self):
        original = dashboard.DEFAULT_SECTION_ID
        dashboard.DEFAULT_SECTION_ID = 42
        try:
            body = _client().get("/").text
        finally:
            dashboard.DEFAULT_SECTION_ID = original
        assert 'id="scan-section"' in body
        assert 'value="42"' in body

    def test_assets_are_served(self):
        client = _client()
        for path in ("/static/app.css", "/static/app.js"):
            assert client.get(path).status_code == 200, path

    def test_documented_v2_selectors_present(self):
        body = _client().get("/").text
        css = _client().get("/static/app.css").text
        for selector in ("stats-grid", "ts-panel", "algo-panel"):
            assert selector in body, f"{selector} absent du balisage"
            assert f".{selector}" in css, f"{selector} absent de la feuille de style"


class TestOutputEncoding:
    def test_url_guards_present_in_bundle(self):
        js = _client().get("/static/app.js").text
        assert "function safeUrl(" in js
        assert "function escAttr(" in js
        assert 'rel="noopener noreferrer"' in js

    def test_dynamic_links_go_through_guards(self):
        js = _client().get("/static/app.js").text
        assert 'href="\' + escAttr(url) + \'"' in js
        assert "esc(href)" not in js

    def test_index_is_a_shell_without_project_data(self, tmp_path):
        db = tmp_path / "xss.db"
        conftest.insert_project(
            conftest.make_analyzer(db),
            topic_id=1,
            score=50,
            title='<script>alert("xss")</script>',
        )
        original = dashboard.DB_PATH
        try:
            body = _client(str(db)).get("/").text
        finally:
            dashboard.DB_PATH = original
        assert "alert(" not in body


class TestPromisingAndTrending:
    def test_promising_returns_only_promising_active(self, tmp_path):
        db = tmp_path / "pr.db"
        analyzer = conftest.make_analyzer(db)
        conftest.insert_project(analyzer, topic_id=1, score=90)
        conftest.insert_project(analyzer, topic_id=2, score=50)
        original = dashboard.DB_PATH
        try:
            rows = _client(str(db)).get("/api/promising").json()
        finally:
            dashboard.DB_PATH = original
        assert [r["topic_id"] for r in rows] == [1]

    def test_promising_include_inactive(self, tmp_path):
        db = tmp_path / "pr2.db"
        analyzer = conftest.make_analyzer(db)
        conftest.insert_project(analyzer, topic_id=1, score=90)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE projects SET is_active = 0 WHERE topic_id = 1")
        conn.commit()
        conn.close()
        original = dashboard.DB_PATH
        try:
            client = _client(str(db))
            assert client.get("/api/promising").json() == []
            rows = client.get("/api/promising?include_inactive=1").json()
        finally:
            dashboard.DB_PATH = original
        assert [r["topic_id"] for r in rows] == [1]

    def test_trending_runs(self, tmp_path):
        db = tmp_path / "tr.db"
        conftest.insert_project(conftest.make_analyzer(db), topic_id=1, score=70)
        original = dashboard.DB_PATH
        try:
            assert _client(str(db)).get("/api/trending").status_code == 200
        finally:
            dashboard.DB_PATH = original


class TestFrontendContract:
    """Vérifie que le DOM, le CSS et le JS statiques restent cohérents entre eux.

    Substitut à un test navigateur : attrape les sélecteurs d'identifiants
    cassés, les classes CSS orphelines et les sprites d'icônes manquants.
    """

    @staticmethod
    def _assets():
        root = Path(__file__).resolve().parent.parent / "static"
        return (
            (root / "index.html").read_text(encoding="utf-8"),
            (root / "app.css").read_text(encoding="utf-8"),
            (root / "app.js").read_text(encoding="utf-8"),
        )

    def test_every_referenced_element_id_exists(self):
        html, _, js = self._assets()
        static_ids = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', html))
        # Certains nœuds naissent dans le HTML produit par app.js (ex. #history).
        dynamic_ids = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', js))
        referenced = set(re.findall(r'\$\("([a-zA-Z0-9_-]+)"\)', js))
        missing = referenced - static_ids - dynamic_ids
        assert missing == set(), f"ids absents du HTML : {sorted(missing)}"

    def test_every_data_hook_exists(self):
        html, _, js = self._assets()
        html_tabs = set(re.findall(r'data-tab="([a-z]+)"', html))
        leaves = set(re.findall(r'data-leaf="([a-z]+)"', html))
        assert html_tabs, "aucun onglet déclaré"
        assert html_tabs == leaves, f"onglets et feuilles désalignés : {html_tabs ^ leaves}"
        # chaque valeur de data-tab gérée par le JS doit exister dans le HTML
        managed = set(re.findall(r'^\s{2}([a-z]+):\s*\{ title:', js, re.M))
        assert managed <= html_tabs, f"onglets gérés mais absents : {sorted(managed - html_tabs)}"

    def test_every_sprite_symbol_exists(self):
        html, _, js = self._assets()
        symbols = set(re.findall(r'<symbol id="([a-z-]+)"', html))
        used = set(re.findall(r'<use href="#([a-z-]+)"', html))
        used |= set(re.findall(r'href="#([a-z-]+)"', js))
        used |= set(re.findall(r'emptyState\([\s\S]{0,200}?,\s*"(i-[a-z-]+)"\)', js))
        assert used <= symbols, f"symboles manquants : {sorted(used - symbols)}"

    def test_emitted_css_classes_are_defined(self):
        _, css, js = self._assets()
        defined = set(re.findall(r'\.([a-z][a-z0-9-]*)', css))
        # ne retient que les littéraux de classe, pas les expressions JS
        emitted = set()
        for match in re.findall(r'class="([a-z0-9 _-]*)"', js):
            emitted |= set(match.split())
        unknown = sorted(c for c in emitted if c not in defined)
        assert unknown == [], f"classes émises sans règle CSS : {unknown}"

    def test_state_modifier_classes_are_defined(self):
        _, css, js = self._assets()
        defined = set(re.findall(r'\.([a-z][a-z0-9-]*)', css))
        emitted = set()
        for match in re.findall(r'class="([a-z0-9 _-]*)"', js):
            emitted |= {c for c in match.split() if c.startswith("is-")}
        # scoreTone() et les variantes de tagList renvoient des modificateurs
        for name in re.findall(r'return "([a-z-]+)"', js):
            if name.startswith("is-"):
                emitted.add(name)
        unknown = sorted(c for c in emitted if c not in defined)
        assert unknown == [], f"modificateurs sans règle CSS : {unknown}"

    def test_single_radius_scale(self):
        _, css, _ = self._assets()
        radii = set(re.findall(r'border-radius:\s*([0-9]+)px', css))
        assert radii <= {"6", "10", "50", "0", "2", "3", "4", "100", "999"}, radii

    def test_llm_panel_is_wired_end_to_end(self):
        """Chaque identifiant du formulaire LLM doit exister dans le DOM et etre
        pilote par app.js : un `#llm-save` absent casse silencieusement la page."""
        html, css, js = self._assets()
        ids = ("llm-box", "llm-summary", "llm-badge", "llm-msg", "llm-models",
               "llm-provider", "llm-model", "llm-ollama-host", "llm-ollama-field",
               "llm-api-base", "llm-base-field", "llm-api-key", "llm-key-field",
               "llm-key-note", "llm-temperature", "llm-timeout",
               "llm-refresh", "llm-models-note", "llm-test", "llm-save")
        for name in ids:
            assert f'id="{name}"' in html, f"identifiant absent du DOM : {name}"
            assert f'$("{name}")' in js, f"identifiant jamais manipule par app.js : {name}"
        for cls in ("llm-box", "llm-summary", "llm-badge", "llm-body",
                    "llm-actions", "llm-msg", "is-input-action"):
            assert f".{cls}" in css, f"classe LLM sans style : {cls}"

    def test_i18n_calls_with_params_use_tfmt(self):
        """`t()` n'interpole pas les parametres : un appel `t("clé", x=y)` affiche
        littéralement `{x}` dans l'interface."""
        _, _, js = self._assets()
        bad = re.findall(r'(?<!f)\bt\(\s*"[a-z0-9_.]+"\s*,\s*[A-Za-z_]', js)
        assert not bad, f"appels t() a parametres : {bad}"

    def test_llm_api_key_never_rendered_into_the_dom(self):
        """Le formulaire ne doit pas pre-remplir la cle depuis la configuration."""
        html, _, js = self._assets()
        assert 'id="llm-api-key"' in html
        assert 'autocomplete="new-password"' in html or 'autocomplete="off"' in html
        assert 'llm-config.json' not in js
        assert 'config_path' not in js

    def test_no_external_asset_references(self):
        html, css, _ = self._assets()
        blob = html + css
        # Les URL sont tolérées en placeholder de champ (hôte LLM saisi par
        # l'utilisateur), jamais en chargement de ressource.
        allowed = ("http://www.w3.org", "http://localhost:11434",
                   "https://api.example.com/v1")
        for url in re.findall(r'https?://[^\s"\'<>)]*', blob):
            assert url.startswith(allowed), f"URL externe non autorisée : {url}"
        assert "cdn" not in blob.lower()
        assert "@import" not in css
        assert "@font-face" not in css


class TestFrontendApiContract:
    """Fige les champs que les rendus de static/app.js consomment.

    Une suppression ou un renommage côté API doit casser un test,
    pas produire un panneau vide dans l'interface.
    """

    STATS = ("db_exists", "total_projects", "active_count", "inactive_count",
             "promising_count", "fork_count", "avg_final_score", "max_final_score",
             "min_final_score", "last_analysis")
    PROJECT = (
        "topic_id", "title", "author", "final_score", "is_active", "is_promising",
        "is_fork", "post_date", "analysis_date", "mining_algorithm", "mining_type",
        "mining_link", "mining_active_pools", "hashrate_str", "tokenomics_score",
        "github_link", "github_stars", "coingecko_id", "whitepaper_link",
        "social_metrics", "source_analysis", "token_distribution", "mining_pool_list",
    )
    SCAN = ("scan_id", "status", "pages_done", "pages_total", "analyzed_count",
            "skipped_count", "topics_found", "error", "section_id", "started_at")

    def setup_method(self):
        _fresh()
        self._original = dashboard.DB_PATH
        _seed(DB, [(7001, 88), (7002, 55)])
        self.client = _client(DB)

    def teardown_method(self):
        _restore(self._original)
        _fresh()

    def test_stats_fields(self):
        body = self.client.get("/api/stats").json()
        assert set(self.STATS) <= set(body)

    def test_timeseries_fields(self):
        body = self.client.get("/api/stats/timeseries?period=month").json()
        assert {"period", "buckets"} <= set(body)
        assert body["period"] == "month"
        assert isinstance(body["buckets"], list)

    def test_by_algorithm_fields(self):
        body = self.client.get("/api/stats/by_algorithm?top=8").json()
        assert {"algorithms", "total"} <= set(body)

    def test_projects_fields(self):
        rows = self.client.get("/api/projects").json()
        assert rows, "la base de test doit contenir des projets"
        assert set(self.PROJECT) <= set(rows[0])

    def test_project_detail_fields(self):
        detail = self.client.get("/api/projects/7001").json()
        assert {"content", "red_flags", "strengths", "unique_features"} <= set(detail)

    def test_history_fields(self):
        body = self.client.get("/api/history/7001").json()
        assert {"history", "topic_id"} <= set(body)
        assert body["topic_id"] == 7001

    def test_scan_status_fields(self):
        assert set(self.SCAN) <= set(self.client.get("/api/scan/status").json())

    def test_trending_and_promising_share_project_shape(self):
        for path in ("/api/trending", "/api/promising"):
            rows = self.client.get(path).json()
            assert isinstance(rows, list), path
            assert set(self.PROJECT) <= set(rows[0]), path

    def test_source_analysis_subfields_reach_the_client(self):
        """app.js lit source_analysis.source_maturity et .source_notes."""
        analyzer = conftest.make_analyzer(DB)
        project = conftest._make_project(
            7003, 70,
            source_analysis=json.dumps(
                {"source_maturity": 60, "code_quality": 75, "source_notes": "ok"}),
        )
        analyzer.save_project(project)
        row = next(r for r in self.client.get("/api/projects").json()
                   if r["topic_id"] == 7003)
        assert row["source_analysis"]["source_maturity"] == 60
        assert row["source_analysis"]["code_quality"] == 75
        assert row["source_analysis"]["source_notes"] == "ok"


class TestStaticPlaceholderSafety:
    """Les placeholders du serveur ne doivent jamais atteindre le navigateur.

    app.js est servi tel quel par StaticFiles : si un placeholder y subsiste,
    il devient une valeur invalide (par exemple une balise de langue rejetee
    par localeCompare).
    """

    def _client(self):
        return _client()

    def test_index_placeholders_are_substituted(self):
        body = self._client().get("/").text
        assert "__DEFAULT_LANG__" not in body
        assert "__DEFAULT_SECTION__" not in body
        assert 'lang="fr"' in body or 'lang="en"' in body

    def test_javascript_has_no_unsubstituted_placeholder(self):
        js = self._client().get("/static/app.js").text
        assert "__DEFAULT" not in js, "placeholder non substitue dans app.js"

    def test_css_has_no_unsubstituted_placeholder(self):
        css = self._client().get("/static/app.css").text
        assert "__DEFAULT" not in css

    def test_locale_compare_receives_a_valid_tag(self):
        """LANG doit etre un tag BCP-47 valide, sinon localeCompare leve."""
        js = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(
            encoding="utf-8")
        assert 'document.documentElement.lang' in js
        assert 'localeCompare(vb, LANG' in js
        # un fallback litteral garantit une valeur valide meme sans <html lang>
        assert 'serverDefault || "fr"' in js

    def test_grid_columns_cannot_overflow_their_container(self):
        """min(Npx, 1fr) sans min-width:0 laisse depasser la piste en mobile."""
        css = (Path(__file__).resolve().parent.parent / "static" / "app.css").read_text(
            encoding="utf-8")
        # La regle de base borne la piste ; les surcharges de media query
        # (une seule colonne) sont correctes telles quelles.
        base = re.search(r'(?<![\w.-])\.project-grid\s*\{([^}]*)\}', css)
        assert base, "regle .project-grid absente"
        assert "min(" in base.group(1), "project-grid sans borne min(Npx, 100%)"
        project = re.search(r'\.project\s*\{([^}]*)\}', css, re.S)
        assert project and "min-width: 0" in project.group(1), \
            ".project sans min-width:0 : le titre line-clamp force la piste"
