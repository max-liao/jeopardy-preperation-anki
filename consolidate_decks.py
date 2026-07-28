#!/usr/bin/env python3
"""Merge the legacy 'Jeopardy' deck into 'Jeopardy Smart Prep'.

The original import left the collection split across two decks: the legacy
'Jeopardy' deck (1984-2019 clues, never scored) and 'Jeopardy Smart Prep'
(2019-2025 clues, scored and tagged). This moves every card into the single
Smart Prep deck and removes the now-empty legacy deck.

Only `cards.did` changes — scheduling state, review history, and manual note
edits are untouched. Anki must be closed.

Usage:
  python consolidate_decks.py [--db PATH] [--dry-run] [--no-backup]
"""

import argparse
import logging
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from jeopardy_consts import (
    ANKI_COLLECTION_PATH,
    DECK_LEGACY_NAME,
    DECK_TARGET_NAME,
    GRAVE_TYPE_DECK,
    USN_PENDING,
)
from jeopardy_db_helpers import connect_anki, require_anki_closed

logger = logging.getLogger(__name__)


def find_deck_id(conn: sqlite3.Connection, name: str) -> int | None:
    """Return the id of the deck named `name`, or None if absent."""
    row = conn.execute("SELECT id FROM decks WHERE name = ?", (name,)).fetchone()
    return int(row[0]) if row else None


def count_cards_in_deck(conn: sqlite3.Connection, deck_id: int) -> int:
    """Return the number of cards currently assigned to `deck_id`."""
    row = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE did = ?", (deck_id,)
    ).fetchone()
    return int(row[0])


def move_cards(conn: sqlite3.Connection, source_id: int, target_id: int) -> int:
    """Reassign every card from `source_id` to `target_id`. Returns rows moved."""
    now = int(time.time())
    cursor = conn.execute(
        "UPDATE cards SET did = ?, mod = ?, usn = ? WHERE did = ?",
        (target_id, now, USN_PENDING, source_id),
    )
    return int(cursor.rowcount)


def delete_deck(conn: sqlite3.Connection, deck_id: int) -> None:
    """Delete a deck and tombstone it so the change propagates on sync."""
    conn.execute(
        "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, ?)",
        (deck_id, GRAVE_TYPE_DECK, USN_PENDING),
    )
    conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))


def mark_collection_modified(conn: sqlite3.Connection) -> None:
    """Bump the collection mtime so Anki notices the change on next open."""
    conn.execute("UPDATE col SET mod = ?", (int(time.time() * 1000),))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=ANKI_COLLECTION_PATH, help="collection.anki2 path"
    )
    parser.add_argument("--source-deck", default=DECK_LEGACY_NAME)
    parser.add_argument("--target-deck", default=DECK_TARGET_NAME)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--no-backup", action="store_true", help="skip the .bak copy")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        sys.exit(1)
    require_anki_closed(db_path)

    conn = connect_anki(db_path)
    source_id = find_deck_id(conn, args.source_deck)
    target_id = find_deck_id(conn, args.target_deck)

    if target_id is None:
        logger.error(
            "Target deck '%s' not found — nothing to merge into", args.target_deck
        )
        conn.close()
        sys.exit(1)
    if source_id is None:
        logger.info(
            "Source deck '%s' not present — already consolidated, nothing to do",
            args.source_deck,
        )
        conn.close()
        return

    source_n = count_cards_in_deck(conn, source_id)
    target_n = count_cards_in_deck(conn, target_id)
    logger.info("'%s' (%d): %d cards", args.source_deck, source_id, source_n)
    logger.info("'%s' (%d): %d cards", args.target_deck, target_id, target_n)
    logger.info("After merge: %d cards in '%s'", source_n + target_n, args.target_deck)

    if args.dry_run:
        logger.info("Dry run — no changes written.")
        conn.close()
        return

    if not args.no_backup:
        backup = db_path.with_name(db_path.name + ".bak")
        shutil.copy2(db_path, backup)
        logger.info("Backed up to %s", backup)

    with conn:
        moved = move_cards(conn, source_id, target_id)
        delete_deck(conn, source_id)
        mark_collection_modified(conn)

    final_n = count_cards_in_deck(conn, target_id)
    orphans = int(
        conn.execute(
            "SELECT COUNT(*) FROM cards WHERE did = ?", (source_id,)
        ).fetchone()[0]
    )
    conn.close()

    logger.info("Moved %d cards; '%s' now holds %d", moved, args.target_deck, final_n)
    if orphans:
        logger.error("%d cards still reference the deleted deck!", orphans)
        sys.exit(1)
    logger.info("✓ Consolidation complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
