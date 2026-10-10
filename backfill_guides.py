"""Remplit `mining_guide_json` pour les projets déjà en base.

La colonne est créée avec le nouveau collecteur, donc les projets analysés
avant restent sans réglages de minage. Ce script les reprend : l'annonce
d'abord, le site du projet ensuite. Aucun valoriseur n'est inventé, une page
qui ne parle pas de minage laisse la colonne vide.

Usage:
    python backfill_guides.py                # rapport, aucune écriture
    python backfill_guides.py --apply        # écrit les guides trouvés
"""

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bitcointalk  # noqa: E402
import mining_guide  # noqa: E402

logger = logging.getLogger("backfill_guides")


async def collect(db_path: str, limit: int) -> list:
    """Guides trouvés pour chaque projet qui n'en a pas encore."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT topic_id, website_link, content FROM projects "
        "WHERE (mining_guide_json IS NULL OR mining_guide_json IN ('', '{}')) "
        "ORDER BY final_score DESC, analysis_date DESC"
    ).fetchall()
    conn.close()

    analyzer = bitcointalk.UltimateBitcointalkAnalyzer(db_path=":memory:")
    await analyzer.init_session()
    found = []
    try:
        for index, row in enumerate(rows[:limit or len(rows)], start=1):
            content = await analyzer.fetch_announcement(row["topic_id"])
            guide = await analyzer.fetch_mining_guide(row["website_link"] or "", content)
            if not mining_guide.guide_is_usable(guide):
                continue
            found.append((row["topic_id"], guide))
            logger.info("%s %s", row["topic_id"], guide.get("stratum") or guide.get("pool"))
            if index % 20 == 0:
                logger.info("%d/%d annonces lues", index, min(limit or len(rows), len(rows)))
    finally:
        await analyzer.close_session()
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="crypto_analysis.db")
    parser.add_argument("--apply", action="store_true", help="écrit les guides")
    parser.add_argument("--limit", type=int, default=0, help="annonces maximum à lire")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    found = asyncio.run(collect(args.db, args.limit))
    logger.info("%d guide(s) trouve(s)", len(found))
    if not args.apply:
        logger.info("aucune ecriture (utiliser --apply)")
        return 0

    conn = sqlite3.connect(args.db)
    conn.executemany(
        "UPDATE projects SET mining_guide_json = ? WHERE topic_id = ?",
        [(json.dumps(guide, ensure_ascii=False), topic_id) for topic_id, guide in found],
    )
    conn.commit()
    conn.close()
    logger.info("guides ecrits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())