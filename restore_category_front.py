#!/usr/bin/env python3
"""Restore {{Category}} to the front of the Jeopardy card, where it belongs.

An earlier pass moved {{Category}} to the back so the front wouldn't hint at the
answer. That wasn't wanted: the category is part of how a Jeopardy clue reads,
so it goes back above the value on the front. The back keeps showing it through
{{FrontSide}}, so the copy added there is removed to avoid printing it twice.

The subject we classified is shown separately, in the frequency badge
(e.g. "89 - Literature"); see add_subject_to_badge.py.

Idempotent: no-ops once the front already has the Category line.

Usage:
  python restore_category_front.py [--db PATH] [--no-backup] [--dry-run]
"""

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from jeopardy_consts import ANKI_COLLECTION_PATH, JEOPARDY_NOTETYPE_ID, USN_PENDING
from jeopardy_db_helpers import (
    connect_anki,
    protobuf_get_field,
    protobuf_replace_fields,
    require_anki_closed,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CATEGORY_LINE: str = "{{Category}} <br>\n"
# The category sits between the round/air-date header and the clue's value.
QFMT_ANCHOR: str = "{{Value}} <br>\n"
# What the back looks like while it carries its own copy of the category.
AFMT_WITH_CATEGORY: str = "<hr id=answer>\n\n{{Category}} <br>\n{{Answer}}"
AFMT_WITHOUT_CATEGORY: str = "<hr id=answer>\n\n{{Answer}}"


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

    conn = connect_anki(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT config FROM templates WHERE ntid = ? AND ord = 0",
        (JEOPARDY_NOTETYPE_ID,),
    )
    row = cur.fetchone()
    if row is None:
        logger.error("Jeopardy template (ord 0) not found")
        conn.close()
        sys.exit(1)
    config: bytes = row[0]

    qfmt_bytes = protobuf_get_field(config, 1)
    afmt_bytes = protobuf_get_field(config, 2)
    if qfmt_bytes is None or afmt_bytes is None:
        logger.error("Front/back template fields not found in config")
        conn.close()
        sys.exit(1)
    qfmt = qfmt_bytes.decode("utf-8")
    afmt = afmt_bytes.decode("utf-8")

    if CATEGORY_LINE in qfmt:
        logger.info("Front template already shows the category — nothing to do")
        conn.close()
        return
    if QFMT_ANCHOR not in qfmt:
        logger.error(
            "Front template anchor %r not found; template has changed since this "
            "script was written — aborting without changes",
            QFMT_ANCHOR,
        )
        conn.close()
        sys.exit(1)

    new_qfmt = qfmt.replace(QFMT_ANCHOR, CATEGORY_LINE + QFMT_ANCHOR, 1)
    new_afmt = afmt.replace(AFMT_WITH_CATEGORY, AFMT_WITHOUT_CATEGORY, 1)

    if args.dry_run:
        logger.info("--- new front template ---\n%s", new_qfmt)
        logger.info("--- new back template ---\n%s", new_afmt)
        logger.info("Dry run — no changes written.")
        conn.close()
        return

    if not args.no_backup:
        backup = db_path.with_name(db_path.name + ".bak")
        shutil.copy2(db_path, backup)
        logger.info("Backed up to %s", backup)

    new_config = protobuf_replace_fields(
        config, {1: new_qfmt.encode("utf-8"), 2: new_afmt.encode("utf-8")}
    )
    now_secs = int(time.time())
    with conn:
        conn.execute(
            "UPDATE templates SET config = ?, mtime_secs = ?, usn = ? "
            "WHERE ntid = ? AND ord = 0",
            (new_config, now_secs, USN_PENDING, JEOPARDY_NOTETYPE_ID),
        )
        conn.execute(
            "UPDATE notetypes SET mtime_secs = ?, usn = ? WHERE id = ?",
            (now_secs, USN_PENDING, JEOPARDY_NOTETYPE_ID),
        )
        conn.execute(
            "UPDATE col SET scm = ?, mod = ?", (now_secs * 1000, now_secs * 1000)
        )
    conn.close()
    logger.info("Restored {{Category}} to the front template.")


if __name__ == "__main__":
    main()
