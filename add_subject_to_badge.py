#!/usr/bin/env python3
"""One-time migration: put the subject label back on the frequency badge.

The badge used to read "freq 89 - high - Literature" but was cut to a bare "89"
because inline styling cost 174 bytes on every one of ~452K notes. This restores
the subject only, using the same trick already used for tier colours: a short
class code on the note (~4 bytes) that a CSS ::after rule in the card template
expands back to the full subject text.

Two things change:
  1. the badge on each note gains its subject's class code
  2. the template's stylesheet is swapped for the one carrying the ::after rules

The subject is read from each note's existing `subject:` tag, so no rescoring is
needed. Notes tagged subject:Other get no code and keep the bare score.

Idempotent: notes that already carry a subject code are left alone.

Usage:
  python add_subject_to_badge.py [--db PATH] [--no-backup] [--dry-run]
"""

import argparse
import logging
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from jeopardy_consts import (
    ANKI_COLLECTION_PATH,
    BADGE_STYLE_BLOCK,
    JEOPARDY_NOTETYPE_ID,
    SUBJECT_BADGE_CLASS,
    USN_PENDING,
)
from jeopardy_db_helpers import (
    connect_anki,
    protobuf_get_field,
    protobuf_replace_fields,
    require_anki_closed,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Matches the badge as smart_prep.py writes it, capturing the class list so an
# already-migrated badge can be detected and skipped.
BADGE_RE = re.compile(r'<b class="fq ([^"]*)">(\d+)</b>')
SUBJECT_CODE_RE = re.compile(r"\bs\d+\b")
# The stylesheet injected by a previous run, which this replaces wholesale.
OLD_STYLE_RE = re.compile(r"<style>/\*fq-badge-css\*/.*?</style>\n?", re.DOTALL)
TAG_SANITIZE_RE = re.compile(r"[^A-Za-z0-9]+")


def sanitize_tag_value(value: str) -> str:
    """Mirror of smart_prep.sanitize_tag_value, for reversing subject tags."""
    return TAG_SANITIZE_RE.sub("_", value).strip("_") or "Unknown"


def build_tag_to_code() -> dict[str, str]:
    """Map a sanitized `subject:` tag value back to its badge class code."""
    return {
        sanitize_tag_value(subject): code
        for subject, code in SUBJECT_BADGE_CLASS.items()
    }


def subject_tag_of(tags: str) -> str | None:
    """Return the sanitized value of a note's `subject:` tag, if it has one."""
    for tag in tags.split():
        if tag.startswith("subject:"):
            return tag[len("subject:") :]
    return None


def update_template_styles(conn: sqlite3.Connection, dry_run: bool) -> bool:
    """Swap the template's badge stylesheet for the one with subject rules.

    Args:
        conn: SQLite connection
        dry_run: Report only; make no changes

    Returns:
        True if the stylesheet was replaced
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT config FROM templates WHERE ntid = ? AND ord = 0",
        (JEOPARDY_NOTETYPE_ID,),
    )
    row = cur.fetchone()
    if row is None:
        raise SystemExit("Jeopardy template (ord 0) not found")
    config: bytes = row[0]

    qfmt_bytes = protobuf_get_field(config, 1)
    if qfmt_bytes is None:
        raise SystemExit("Front template not found in config")
    qfmt = qfmt_bytes.decode("utf-8")

    if BADGE_STYLE_BLOCK in qfmt:
        logger.info("Template stylesheet already current")
        return False

    if OLD_STYLE_RE.search(qfmt):
        new_qfmt = OLD_STYLE_RE.sub(BADGE_STYLE_BLOCK, qfmt, count=1)
    else:
        new_qfmt = BADGE_STYLE_BLOCK + qfmt

    if dry_run:
        logger.info("--- new front template ---\n%s", new_qfmt)
        return True

    new_config = protobuf_replace_fields(config, {1: new_qfmt.encode("utf-8")})
    now_secs = int(time.time())
    cur.execute(
        "UPDATE templates SET config = ?, mtime_secs = ?, usn = ? "
        "WHERE ntid = ? AND ord = 0",
        (new_config, now_secs, USN_PENDING, JEOPARDY_NOTETYPE_ID),
    )
    cur.execute(
        "UPDATE notetypes SET mtime_secs = ?, usn = ? WHERE id = ?",
        (now_secs, USN_PENDING, JEOPARDY_NOTETYPE_ID),
    )
    cur.execute("UPDATE col SET scm = ?, mod = ?", (now_secs * 1000, now_secs * 1000))
    logger.info("Replaced template stylesheet (%d bytes)", len(BADGE_STYLE_BLOCK))
    return True


def migrate_badges(conn: sqlite3.Connection, dry_run: bool) -> tuple[int, int, int]:
    """Add the subject class code to every note's badge.

    Args:
        conn: SQLite connection
        dry_run: Report only; make no changes

    Returns:
        (updated, skipped_already_done, skipped_no_subject)
    """
    tag_to_code = build_tag_to_code()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, flds, tags FROM notes WHERE mid = ?", (JEOPARDY_NOTETYPE_ID,)
    )
    updates: list[tuple[str, int]] = []
    already = 0
    no_subject = 0
    for note_id, flds, tags in cur.fetchall():
        parts = flds.split("\x1f")
        match = BADGE_RE.fullmatch(parts[-1])
        if match is None:
            no_subject += 1
            continue
        classes, score = match.group(1), match.group(2)
        if SUBJECT_CODE_RE.search(classes):
            already += 1
            continue
        tag_value = subject_tag_of(tags)
        code = tag_to_code.get(tag_value or "")
        if code is None:
            # subject:Other, or no subject tag: bare score is correct.
            no_subject += 1
            continue
        parts[-1] = f'<b class="fq {classes} {code}">{score}</b>'
        updates.append(("\x1f".join(parts), note_id))

    if not dry_run and updates:
        now_secs = int(time.time())
        cur.executemany(
            "UPDATE notes SET flds = ?, mod = ?, usn = ? WHERE id = ?",
            [(flds, now_secs, USN_PENDING, nid) for flds, nid in updates],
        )
    return len(updates), already, no_subject


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=ANKI_COLLECTION_PATH)
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        sys.exit(1)
    require_anki_closed(db_path)

    if not args.dry_run and not args.no_backup:
        backup = db_path.with_name(db_path.name + ".bak")
        shutil.copy2(db_path, backup)
        logger.info("Backed up to %s", backup)

    conn = connect_anki(db_path)
    with conn:
        update_template_styles(conn, args.dry_run)
        updated, already, no_subject = migrate_badges(conn, args.dry_run)

    logger.info(
        "Badges: %d updated, %d already had a subject, %d left bare (Other/untagged)",
        updated,
        already,
        no_subject,
    )
    if args.dry_run:
        logger.info("Dry run — no changes written.")
        conn.close()
        return

    conn.close()
    if not updated:
        # Stylesheet-only re-run (e.g. changing BADGE_SEPARATOR): no note rows
        # were rewritten, so there is no free-page churn to reclaim.
        logger.info("No notes rewritten — skipping VACUUM")
        return
    # Bulk note rewrites leave free-page churn that only VACUUM reclaims.
    vac = connect_anki(db_path)
    vac.execute("VACUUM")
    vac.close()
    logger.info("VACUUM complete — %d bytes", db_path.stat().st_size)


if __name__ == "__main__":
    main()
