#!/usr/bin/env python3
"""Update Anki Jeopardy collection with post-2019 clues from jwolle1 dataset.

Merges TSV clues (2020-2025) into existing .colpkg, avoiding duplicates.
Creates new notes and cards for imported clues.
"""

import argparse
import csv
import logging
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from jeopardy_consts import (
    JEOPARDY_NOTETYPE_ID,
    ROUND_CODE_TO_NAME,
    TOTAL_FIELDS,
    TSV_AIR_DATE,
    TSV_ANSWER,
    TSV_CATEGORY,
    TSV_CLUE_VALUE,
    TSV_COMMENTS,
    TSV_DAILY_DOUBLE_VALUE,
    TSV_QUESTION,
    TSV_ROUND,
)
from jeopardy_db_helpers import (
    connect_anki,
    extract_colpkg,
    field_checksum,
    get_deck_id,
    get_max_new_due,
    get_next_card_id,
    get_next_id,
    pack_fields,
    repack_colpkg,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger(__name__)


def normalize_round(tsv_round: str) -> str:
    """Map a TSV round code to the Anki round name.

    The jwolle1 dataset encodes the round as "1" (Single Jeopardy),
    "2" (Double Jeopardy), or "3" (Final Jeopardy). Unknown codes fall back
    to the trimmed raw value.

    Args:
        tsv_round: Round code from TSV ("1", "2", or "3")

    Returns:
        Anki round name (e.g., "Jeopardy", "Double Jeopardy", "Final Jeopardy")
    """
    code = tsv_round.strip()
    return ROUND_CODE_TO_NAME.get(code, code)


def parse_tsv_clues(tsv_path: Path, cutoff_date: str) -> list[dict[str, Any]]:
    """Parse TSV file and filter to clues after cutoff date.

    Args:
        tsv_path: Path to combined_season1-41.tsv
        cutoff_date: Minimum air date (YYYY-MM-DD format, inclusive)

    Returns:
        List of clue dicts with TSV columns
    """
    # Some clue/answer fields are long; lift csv's default field-size cap.
    csv.field_size_limit(10 * 1024 * 1024)

    clues: list[dict[str, Any]] = []
    with open(tsv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("TSV has no header row")

        for row in reader:
            air_date = row.get(TSV_AIR_DATE, "")
            # Strictly after the cutoff: the existing collection already covers
            # everything up to and including cutoff_date, so `>` avoids dupes.
            if air_date > cutoff_date:
                clues.append(row)

    logger.info(f"Parsed {len(clues)} clues from {tsv_path} (after {cutoff_date})")
    return clues


def clue_to_note_fields(clue: dict[str, Any]) -> list[str]:
    """Convert TSV clue to Anki note fields.

    Args:
        clue: TSV clue dict

    Returns:
        List of 14 field values in Anki order
    """
    # Get components
    round_name = normalize_round(clue.get(TSV_ROUND, ""))
    clue_value = clue.get(TSV_CLUE_VALUE, "")
    daily_double_value = (clue.get(TSV_DAILY_DOUBLE_VALUE, "") or "").strip()
    # daily_double_value is "0" (string) when the clue is NOT a Daily Double,
    # and the wagered amount otherwise — so check it's present and non-zero.
    has_daily_double = bool(daily_double_value) and daily_double_value != "0"

    value_str = f"${clue_value}" if clue_value and clue_value != "0" else ""

    # NOTE: answer/question are reversed between the TSV and the Anki deck.
    # TSV `answer` (the clue shown) -> Anki Question (field 9)
    # TSV `question` (the response) -> Anki Answer (field 11)
    fields: list[str] = [
        "",  # 0: Show number (not in TSV)
        clue.get(TSV_AIR_DATE, ""),  # 1: AirDate
        clue.get(TSV_COMMENTS, ""),  # 2: Extra Info
        round_name,  # 3: Round
        "",  # 4: Coords (not in TSV)
        clue.get(TSV_CATEGORY, ""),  # 5: Category
        "",  # 6: Order (not in TSV)
        value_str,  # 7: Value
        "True" if has_daily_double else "False",  # 8: Daily Double
        clue.get(TSV_ANSWER, ""),  # 9: Question (TSV `answer` = clue shown)
        "",  # 10: Links (no media in TSV)
        clue.get(TSV_QUESTION, ""),  # 11: Answer (TSV `question` = response)
        "0",  # 12: Correct Attempts
        "0",  # 13: Wrong Attempts
    ]

    if len(fields) != TOTAL_FIELDS:
        raise ValueError(f"Expected {TOTAL_FIELDS} fields, got {len(fields)}")

    return fields


def insert_clues(
    conn: sqlite3.Connection,
    clues: list[dict[str, Any]],
    deck_id: int,
) -> int:
    """Insert clues as notes and cards into database.

    Args:
        conn: SQLite connection to collection.anki2
        clues: List of parsed clues
        deck_id: Deck ID to insert into

    Returns:
        Number of clues inserted
    """
    cursor = conn.cursor()

    # ID offsets — notes and cards have separate id spaces, so derive each
    # from its own table to guarantee uniqueness.
    next_note_id = get_next_id(conn)
    next_card_id = get_next_card_id(conn)
    max_new_due = get_max_new_due(conn)
    next_due = max_new_due + 1

    # Current time in milliseconds
    now_ms = int(time.time() * 1000)

    # Prepare bulk insert data
    note_rows: list[tuple[Any, ...]] = []
    card_rows: list[tuple[Any, ...]] = []

    for i, clue in enumerate(clues):
        # Note row
        note_id = next_note_id + i
        fields = clue_to_note_fields(clue)
        flds = pack_fields(fields)
        sort_field = fields[0]  # sfld = first field (Show number), per notetype

        note_rows.append(
            (
                note_id,  # id
                f"jio-{now_ms}-{i}",  # guid — unique across the collection
                JEOPARDY_NOTETYPE_ID,  # mid (model/notetype ID)
                now_ms,  # mod (modification timestamp)
                -1,  # usn (review sync state)
                "",  # tags (will be added by smart_prep.py)
                flds,  # flds (field data)
                sort_field,  # sfld (sort field — first field value)
                field_checksum(sort_field),  # csum (first-field checksum)
                0,  # flags
                "",  # data
            )
        )

        # Card row
        card_id = next_card_id + i
        card_rows.append(
            (
                card_id,  # id
                note_id,  # nid (note ID)
                deck_id,  # did (deck ID)
                0,  # ord (template ordinal — single Jeopardy template)
                now_ms,  # mod
                -1,  # usn
                0,  # type (0=new)
                0,  # queue (0=new)
                next_due + i,  # due (new-card order)
                0,  # ivl (interval)
                0,  # factor (ease)
                0,  # reps (review count)
                0,  # lapses (lapse count)
                0,  # left (reps left today)
                0,  # odue (original due)
                0,  # odid (original deck)
                0,  # flags
                "",  # data
            )
        )

    # Batch insert
    cursor.executemany(
        """INSERT INTO notes
        (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        note_rows,
    )

    cursor.executemany(
        """INSERT INTO cards
        (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        card_rows,
    )

    conn.commit()
    logger.info(f"Inserted {len(clues)} notes and cards")
    return len(clues)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Merge post-2019 Jeopardy clues into existing collection"
    )
    parser.add_argument("source", help="Source .colpkg file")
    parser.add_argument("tsv", help="TSV clue file (combined_season1-41.tsv)")
    parser.add_argument("output", help="Output .colpkg file")
    parser.add_argument(
        "--cutoff-date",
        default="2019-06-06",
        help="Minimum air date to import (YYYY-MM-DD, default: 2019-06-06)",
    )

    args = parser.parse_args()

    source_path = Path(args.source)
    tsv_path = Path(args.tsv)
    output_path = Path(args.output)

    if not source_path.exists():
        logger.error(f"Source file not found: {source_path}")
        sys.exit(1)

    if not tsv_path.exists():
        logger.error(f"TSV file not found: {tsv_path}")
        sys.exit(1)

    try:
        # Temporary directory for extraction
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            logger.info(f"Extracting {source_path}")
            db_path = extract_colpkg(source_path, tmp_path)

            logger.info(f"Opening database at {db_path}")
            conn = connect_anki(db_path)

            # Get deck ID
            deck_id = get_deck_id(conn)
            logger.info(f"Using deck ID: {deck_id}")

            # Parse TSV
            logger.info(f"Parsing {tsv_path}")
            clues = parse_tsv_clues(tsv_path, args.cutoff_date)

            if not clues:
                logger.warning(f"No clues found after {args.cutoff_date}")
                sys.exit(1)

            # Insert clues
            logger.info(f"Inserting {len(clues)} clues")
            count = insert_clues(conn, clues, deck_id)

            conn.close()

            # Repack
            extract_dir = tmp_path / "extract"
            logger.info(f"Repacking to {output_path}")
            repack_colpkg(db_path, extract_dir, output_path)

            logger.info(f"✓ Success! Added {count} notes ({args.cutoff_date} onwards)")

    except Exception as e:
        logger.exception(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
