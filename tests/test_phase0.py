import sqlite3


def _open(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def test_is_active_column(tmp_path):
    from conftest import make_analyzer

    db = tmp_path / "phase0.db"
    make_analyzer(db)
    conn = _open(db)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(projects)")]
    conn.close()
    assert "is_active" in cols


def test_indexes_exist(tmp_path):
    from conftest import make_analyzer

    db = tmp_path / "phase0.db"
    make_analyzer(db)
    conn = _open(db)
    names = {r["name"] for r in conn.execute("PRAGMA index_list(projects)")}
    conn.close()
    assert "idx_projects_analysis_date" in names
    assert "idx_projects_mining_algorithm" in names
    assert "idx_projects_is_active" in names


def test_user_version(tmp_path):
    from conftest import make_analyzer

    db = tmp_path / "phase0.db"
    make_analyzer(db)
    conn = _open(db)
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert ver == 1
