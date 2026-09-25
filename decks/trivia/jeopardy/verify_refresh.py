#!/usr/bin/env python3
"""Prove that a smart_prep refresh left your study data untouched.

A refresh may rewrite only the frequency badge (field 14), the frequency details
(field 15), and the tags of Jeopardy notes. `snapshot` fingerprints everything
else: the review log, every card row (IDs and scheduling), and each note's
identity and content fields. `compare` fingerprints again after the refresh and
exits non-zero if anything differs.

Usage:
  python -m decks.trivia.jeopardy.verify_refresh snapshot DB SNAPSHOT.json   # before the refresh
  python -m decks.trivia.jeopardy.verify_refresh compare  DB SNAPSHOT.json   # after the refresh
"""

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final, NamedTuple

from decks.trivia.jeopardy.jeopardy_consts import JEOPARDY_NOTETYPE_ID, TOTAL_FIELDS
from decks.trivia.jeopardy.jeopardy_db_helpers import connect_anki, require_anki_closed

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CMD_SNAPSHOT: Final[str] = "snapshot"
CMD_COMPARE: Final[str] = "compare"
COMPONENT_REVLOG: Final[str] = "revlog"
COMPONENT_CARDS: Final[str] = "cards"
COMPONENT_NOTES: Final[str] = "notes"
COMPONENTS: Final[tuple[str, ...]] = (
    COMPONENT_REVLOG,
    COMPONENT_CARDS,
    COMPONENT_NOTES,
)
FIELD_SEPARATOR: Final[str] = "\x1f"


class ComponentFingerprint(NamedTuple):
    """Row count and content hash of one part of the collection."""

    rows: int
    sha256: str


# component name -> fingerprint, for everything a refresh must not change
Fingerprint = dict[str, ComponentFingerprint]


def hash_rows(rows: Iterable[Sequence[object]]) -> str:
    """SHA-256 over the rows, in the order given."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def fingerprint_rows(rows: Sequence[Sequence[object]]) -> ComponentFingerprint:
    """Fingerprint a fully materialised list of rows."""
    return ComponentFingerprint(rows=len(rows), sha256=hash_rows(rows))


def immutable_note_rows(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    """Each note minus what a refresh legitimately rewrites.

    Jeopardy notes contribute their identity columns and fields 0-13 (the badge,
    the details and the tags are excluded). Every other notetype contributes all
    of its columns, since a refresh has no business touching those at all.
    """
    cursor = conn.execute(
        "SELECT id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data "
        "FROM notes ORDER BY id"
    )
    rows: list[tuple[object, ...]] = []
    for nid, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data in cursor:
        if mid == JEOPARDY_NOTETYPE_ID:
            content = tuple(flds.split(FIELD_SEPARATOR)[:TOTAL_FIELDS])
            rows.append((nid, guid, mid, mod, usn, sfld, csum, flags, data, content))
        else:
            rows.append((nid, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data))
    return rows


def take_fingerprint(conn: sqlite3.Connection) -> Fingerprint:
    """Fingerprint the review log, the cards, and the notes' immutable content."""
    return {
        COMPONENT_REVLOG: fingerprint_rows(
            conn.execute("SELECT * FROM revlog ORDER BY id").fetchall()
        ),
        COMPONENT_CARDS: fingerprint_rows(
            conn.execute("SELECT * FROM cards ORDER BY id").fetchall()
        ),
        COMPONENT_NOTES: fingerprint_rows(immutable_note_rows(conn)),
    }


def differences(before: Fingerprint, after: Fingerprint) -> list[str]:
    """Describe every component that changed between two fingerprints.

    Returns:
        One message per changed or missing component (empty if nothing changed)
    """
    problems: list[str] = []
    for name in COMPONENTS:
        old, new = before.get(name), after.get(name)
        if old is None or new is None:
            problems.append(f"{name}: missing from a fingerprint")
        elif old.rows != new.rows:
            problems.append(f"{name}: rows {old.rows} -> {new.rows}")
        elif old.sha256 != new.sha256:
            problems.append(f"{name}: same count but content changed")
    return problems


def save_snapshot(path: Path, fingerprint: Fingerprint) -> None:
    """Write a fingerprint as JSON."""
    serializable = {
        name: {"rows": fp.rows, "sha256": fp.sha256} for name, fp in fingerprint.items()
    }
    path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")


def load_snapshot(path: Path) -> Fingerprint:
    """Read a fingerprint written by `save_snapshot`."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        name: ComponentFingerprint(rows=int(item["rows"]), sha256=str(item["sha256"]))
        for name, item in raw.items()
    }


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Snapshot / verify that a refresh left study data untouched"
    )
    parser.add_argument("command", choices=(CMD_SNAPSHOT, CMD_COMPARE))
    parser.add_argument("db", help="Path to collection.anki2 (Anki must be closed)")
    parser.add_argument("snapshot", help="Snapshot JSON to write or compare against")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    snapshot_path = Path(args.snapshot)
    if not db_path.exists():
        logger.error(f"DB not found: {db_path}")
        sys.exit(1)
    require_anki_closed(db_path)

    conn = connect_anki(db_path)
    current = take_fingerprint(conn)
    conn.close()

    if args.command == CMD_SNAPSHOT:
        save_snapshot(snapshot_path, current)
        logger.info(
            f"Snapshot written to {snapshot_path}: "
            f"{current[COMPONENT_REVLOG].rows:,} review-log rows, "
            f"{current[COMPONENT_CARDS].rows:,} cards, "
            f"{current[COMPONENT_NOTES].rows:,} notes"
        )
        return

    before = load_snapshot(snapshot_path)
    logger.info(
        f"Review-log rows: {before[COMPONENT_REVLOG].rows:,} before, "
        f"{current[COMPONENT_REVLOG].rows:,} after"
    )
    problems = differences(before, current)
    if problems:
        for problem in problems:
            logger.error(f"CHANGED  {problem}")
        logger.error("Study data was modified. Restore the backup before continuing.")
        sys.exit(1)
    logger.info("OK: review log, cards, and note content are byte-for-byte unchanged")


if __name__ == "__main__":
    main()
