import asyncio
import os
import sys
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
    )
    base.update(overrides)
    return bitcointalk.CryptoProject(**base)


def _run(coro):
    return asyncio.run(coro)


import bitcointalk  # noqa: E402


def make_analyzer(db_path):
    return bitcointalk.UltimateBitcointalkAnalyzer(db_path=str(db_path), timeout=5)


def insert_project(analyzer, topic_id, score, **overrides):
    p = _make_project(topic_id, score, **overrides)
    analyzer.save_project(p)
    return p
