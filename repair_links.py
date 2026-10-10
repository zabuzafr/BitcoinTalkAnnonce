"""Répare les liens de projet stockés en base.

Les annonces sont du texte brut collé : `github.com/o/r📦`, deux URL bout à
bout, une rubrique d'annonce agglutinée au chemin. L'interface nettoie déjà à la
lecture (`dashboard._stored_link`) ; ce script répare les données elles-mêmes,
sans quoi le lien reste pollué pour tout ce qui lit la base directement.

Usage:
    python repair_links.py                 # rapport, aucune écriture
    python repair_links.py --apply         # écrit les corrections
    python repair_links.py --apply --verify  # confirme les URL ambiguës par requête
"""

import argparse
import asyncio
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bitcointalk  # noqa: E402
import urlclean  # noqa: E402

COLUMNS = ("github_link", "website_link", "whitepaper_link")
KIND = {
    "github_link": "github",
    "website_link": "website",
    "whitepaper_link": "whitepaper",
}

logger = logging.getLogger("repair_links")


def repair(raw: str, kind: str) -> str:
    """Valeur corrigée pour une colonne, ou chaîne vide si inexploitable."""
    value = str(raw or "").strip()
    if not value:
        return ""
    if kind == "github":
        return urlclean.canonical_github(value)
    cleaned = urlclean.clean_url(value)
    if kind == "website" and cleaned and not urlclean.is_project_site(cleaned):
        return ""
    return cleaned


def collect(conn: sqlite3.Connection) -> list:
    """Lignes à corriger, avec l'ancien et le nouveau lien."""
    rows = conn.execute(
        "SELECT topic_id, " + ", ".join(COLUMNS) + " FROM projects"
    ).fetchall()
    pending = []
    for row in rows:
        updates = {}
        for column in COLUMNS:
            current = row[column] or ""
            new = repair(current, KIND[column])
            if new != current:
                updates[column] = (current, new)
        if updates:
            pending.append((row["topic_id"], updates))
    return pending


def apply_pending(conn: sqlite3.Connection, pending: list) -> None:
    for topic_id, updates in pending:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = [new for _, new in updates.values()] + [topic_id]
        conn.execute(
            f"UPDATE projects SET {assignments} WHERE topic_id = ?", values
        )
    conn.commit()


def ambiguous_links(conn: sqlite3.Connection) -> list:
    """Lignes dont un lien reste ambigu, avec les variantes à tester.

    `whitepaperWho` peut être `/whitepaper` suivi d'un mot collé, ou une page
    vraiment nommée ainsi. Seul le serveur peut trancher.
    """
    rows = conn.execute(
        "SELECT topic_id, " + ", ".join(COLUMNS) + " FROM projects"
    ).fetchall()
    pending = []
    for row in rows:
        updates = {}
        for column in COLUMNS:
            current = row[column] or ""
            if len(urlclean.link_variants(current)) > 1:
                updates[column] = (current, urlclean.link_variants(current))
        if updates:
            pending.append((row["topic_id"], updates))
    return pending


async def resolve_all(pending: list) -> list:
    """Confirme chaque lien ambigu, sans requête quand il n'y a rien à trancher."""
    analyzer = bitcointalk.UltimateBitcointalkAnalyzer(db_path=":memory:")
    await analyzer.init_session()
    try:
        resolved = []
        for topic_id, updates in pending:
            confirmed = {}
            for column, (current, variants) in updates.items():
                answer = await analyzer.resolve_link(current)
                if answer != current:
                    confirmed[column] = (current, answer)
            if confirmed:
                resolved.append((topic_id, confirmed))
        return resolved
    finally:
        await analyzer.close_session()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="crypto_analysis.db")
    parser.add_argument("--apply", action="store_true", help="écrit les corrections")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="confirme les URL ambiguës par requête avant de corriger",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    pending = collect(conn)
    if args.verify:
        by_topic = {topic_id: updates for topic_id, updates in pending}
        for topic_id, confirmed in asyncio.run(resolve_all(ambiguous_links(conn))):
            by_topic.setdefault(topic_id, {}).update(confirmed)
        pending = [(topic_id, by_topic[topic_id]) for topic_id in sorted(by_topic)]
    for topic_id, updates in pending:
        for column, (current, new) in updates.items():
            logger.info("%s %s: %r -> %r", topic_id, column, current, new)
    logger.info("%d projet(s) a corriger", len(pending))
    if args.apply:
        apply_pending(conn, pending)
        logger.info("corrections ecrites")
    else:
        logger.info("aucune ecriture (utiliser --apply)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())