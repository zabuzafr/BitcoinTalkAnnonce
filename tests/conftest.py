import asyncio
import atexit
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("BT_LOG_FILE", "/tmp/bt_test.log")


def _make_project(topic_id, score, **overrides):
    base = dict(
        topic_id=topic_id,
        title=f"Test project {topic_id}",
        author="tester",
        content=f"Content for {topic_id}",
        post_date="2026-01-01T00:00:00",
        innovation_score=80,
        technical_score=70,
        disruptiveness_score=60,
        credibility_score=60,
        risk_score=10,
        final_score=score,
        analysis_date="2026-01-01T00:00:00",
    )
    base.update(overrides)
    return bitcointalk.CryptoProject(**base)


def _run(coro):
    return asyncio.run(coro)


import bitcointalk  # noqa: E402

# Bases creees pour remapper ":memory:" : nettoyees en fin de session.
_TEMP_DBS: list = []


@atexit.register
def _cleanup_temp_dbs() -> None:
    for path in _TEMP_DBS:
        try:
            os.unlink(path)
        except OSError:
            pass


def make_analyzer(db_path):
    """Builds an analyzer on a real file.

    ``:memory:`` is remapped to a temporary file because the analyzer opens a
    fresh connection per operation; a shared in-memory database would lose the
    schema between ``init_database`` and the next call.
    """
    if str(db_path) == ":memory:":
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = tmp.name
        _TEMP_DBS.append(tmp.name)
    return bitcointalk.UltimateBitcointalkAnalyzer(db_path=str(db_path), timeout=5)


def insert_project(analyzer, topic_id, score, **overrides):
    p = _make_project(topic_id, score, **overrides)
    analyzer.save_project(p)
    return p
