import sqlite3

from fastapi.testclient import TestClient

import dashboard
from conftest import make_analyzer, insert_project


def _client(db_path):
    original = dashboard.DB_PATH
    dashboard.DB_PATH = str(db_path)
    return TestClient(dashboard.app), original


def _restore(original):
    dashboard.DB_PATH = original


def _seed(db_path, projects):
    analyzer = make_analyzer(db_path)
    for topic_id, score in projects:
        insert_project(analyzer, topic_id=topic_id, score=score)
    return analyzer


class TestDashboardPage:
    def test_returns_html(self):
        client, original = _client(":memory:")
        try:
            response = client.get("/")
            assert response.status_code == 200
            assert "html" in response.headers["content-type"]
        finally:
            _restore(original)


class TestApiStats:
    def test_db_missing(self, tmp_path):
        client, original = _client(tmp_path / "missing.db")
        try:
            response = client.get("/api/stats")
            assert response.status_code == 200
            data = response.json()
            assert data["db_exists"] is False
            assert data["total_projects"] == 0
        finally:
            _restore(original)

    def test_db_with_projects(self, tmp_path):
        db = tmp_path / "stats.db"
        _seed(db, [(101, 85), (102, 60)])
        client, original = _client(db)
        try:
            response = client.get("/api/stats")
            assert response.status_code == 200
            data = response.json()
            assert data["db_exists"] is True
            assert data["total_projects"] == 2
            assert data["promising_count"] == 1
        finally:
            _restore(original)


class TestApiProjects:
    def test_returns_list(self, tmp_path):
        db = tmp_path / "proj.db"
        _seed(db, [(201, 90), (202, 40)])
        client, original = _client(db)
        try:
            response = client.get("/api/projects")
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 2
            assert {p["topic_id"] for p in data} == {201, 202}
        finally:
            _restore(original)

    def test_empty_db(self, tmp_path):
        db = tmp_path / "empty.db"
        _seed(db, [])
        client, original = _client(db)
        try:
            response = client.get("/api/projects")
            assert response.status_code == 200
            assert response.json() == []
        finally:
            _restore(original)


class TestApiProjectDetail:
    def test_existing_project(self, tmp_path):
        db = tmp_path / "detail.db"
        _seed(db, [(301, 88)])
        client, original = _client(db)
        try:
            response = client.get("/api/projects/301")
            assert response.status_code == 200
            data = response.json()
            assert data["topic_id"] == 301
            assert data["final_score"] == 88
        finally:
            _restore(original)

    def test_missing_project(self, tmp_path):
        db = tmp_path / "missing_proj.db"
        _seed(db, [])
        client, original = _client(db)
        try:
            response = client.get("/api/projects/999")
            assert response.status_code == 404
        finally:
            _restore(original)

    def test_non_integer_id(self, tmp_path):
        db = tmp_path / "bad_id.db"
        client, original = _client(db)
        try:
            response = client.get("/api/projects/notanint")
            assert response.status_code == 422
        finally:
            _restore(original)


class TestApiHistory:
    def test_existing_topic(self, tmp_path):
        db = tmp_path / "hist.db"
        _seed(db, [(401, 77)])
        client, original = _client(db)
        try:
            response = client.get("/api/history/401")
            assert response.status_code == 200
            data = response.json()
            assert data["topic_id"] == 401
            assert isinstance(data["history"], list)
            assert len(data["history"]) >= 1
        finally:
            _restore(original)

    def test_unknown_topic(self, tmp_path):
        db = tmp_path / "hist404.db"
        _seed(db, [])
        client, original = _client(db)
        try:
            response = client.get("/api/history/999")
            assert response.status_code == 404
        finally:
            _restore(original)


class TestApiI18n:
    def test_languages_listed(self):
        client, original = _client(":memory:")
        try:
            response = client.get("/api/i18n/languages")
            assert response.status_code == 200
            langs = response.json()
            assert isinstance(langs, list)
            codes = {item["code"] for item in langs if isinstance(item, dict)}
            assert "fr" in codes
            assert "en" in codes
        finally:
            _restore(original)

    def test_catalog_fr(self):
        client, original = _client(":memory:")
        try:
            response = client.get("/api/i18n/fr")
            assert response.status_code == 200
            catalog = response.json()
            assert isinstance(catalog, dict)
            assert len(catalog) > 0
        finally:
            _restore(original)

    def test_catalog_en(self):
        client, original = _client(":memory:")
        try:
            response = client.get("/api/i18n/en")
            assert response.status_code == 200
            assert isinstance(response.json(), dict)
        finally:
            _restore(original)

    def test_catalog_unknown_lang_falls_back(self):
        client, original = _client(":memory:")
        try:
            response = client.get("/api/i18n/zz")
            assert response.status_code == 200
            assert isinstance(response.json(), dict)
        finally:
            _restore(original)


class TestSchemaAtStartup:
    """Le dashboard lit des colonnes qu'il ne construit pas toujours : sans
    migration, une base d'une version antérieure casse /api/projects."""

    def test_ensure_schema_creates_missing_table(self, tmp_path):
        import bitcointalk
        db = tmp_path / "fresh.db"
        bitcointalk.ensure_schema(str(db))
        import sqlite3
        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
        conn.close()
        assert "topic_id" in cols
        assert "whitepaper_text" in cols

    def test_ensure_schema_migrates_old_table(self, tmp_path):
        import bitcointalk
        db = tmp_path / "old.db"
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY,"
                     " topic_id INTEGER, title TEXT)")
        conn.commit()
        conn.close()
        bitcointalk.ensure_schema(str(db))
        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
        conn.close()
        assert "content" in cols
        assert "whitepaper_text" in cols


class TestListPayload:
    """Le corps complet des sujets ne sort qu'en détail : la liste ne sert
    qu'au tableau de bord."""

    def test_list_omits_content_detail_includes_it(self, tmp_path):
        db = tmp_path / "content.db"
        _seed(db, [(701, 80)])
        client, original = _client(db)
        try:
            listing = client.get("/api/projects").json()[0]
            assert "content" not in listing
            assert "whitepaper_text" in listing, "utilisé par la couverture"
            detail = client.get("/api/projects/701").json()
            assert detail["content"] == "Content for 701"
        finally:
            _restore(original)
