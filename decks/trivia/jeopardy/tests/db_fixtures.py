"""Minimal Anki-shaped SQLite fixtures, so tests need no Anki install or real deck."""

import sqlite3
from typing import Final

from decks.trivia.jeopardy.jeopardy_consts import (
    FIELD_AIR_DATE,
    FIELD_ANSWER,
    FIELD_CATEGORY,
    FIELD_DAILY_DOUBLE,
    FIELD_QUESTION,
    FIELD_ROUND,
    FIELD_VALUE,
    JEOPARDY_NOTETYPE_ID,
    TOTAL_FIELDS,
)

FIELD_SEPARATOR: Final[str] = "\x1f"
CARD_ID_STRIDE: Final[int] = 10  # card id = note id * stride; keeps ids distinct
REVIEWED_INTERVAL_DAYS: Final[int] = 21


def create_collection_schema(conn: sqlite3.Connection) -> None:
    """Create the notes/cards/revlog tables with the real Anki column layout."""
    conn.execute(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, "
        "mod INTEGER, usn INTEGER, tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, "
        "flags INTEGER, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, "
        "ord INTEGER, mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, "
        "due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER, lapses INTEGER, "
        "left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, "
        "ease INTEGER, ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, "
        "type INTEGER)"
    )


def jeopardy_fields(
    category: str, answer: str, clue: str = "a clue", air_date: str = "2024-05-01"
) -> str:
    """Pack the 14 native Jeopardy fields into an Anki `flds` string."""
    fields = [""] * TOTAL_FIELDS
    fields[FIELD_AIR_DATE] = air_date
    fields[FIELD_ROUND] = "Jeopardy"
    fields[FIELD_CATEGORY] = category
    fields[FIELD_VALUE] = "$400"
    fields[FIELD_DAILY_DOUBLE] = "False"
    fields[FIELD_QUESTION] = clue
    fields[FIELD_ANSWER] = answer
    return FIELD_SEPARATOR.join(fields)


def add_reviewed_jeopardy_note(
    conn: sqlite3.Connection,
    note_id: int,
    category: str,
    answer: str,
    air_date: str = "2024-05-01",
    clue: str = "a clue",
) -> None:
    """Insert a Jeopardy note with a scheduled, already-reviewed card and one review.

    Args:
        conn: connection to a database made by `create_collection_schema`
        note_id: note id; also seeds the card and review-log ids
        category: on-air category (field 5)
        answer: correct response (field 11)
        air_date: YYYY-MM-DD (field 1)
        clue: the clue shown (field 9)
    """
    card_id = note_id * CARD_ID_STRIDE
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, ?, 1, 0, '', ?, ?, 0, 0, '')",
        (
            note_id,
            f"guid{note_id}",
            JEOPARDY_NOTETYPE_ID,
            jeopardy_fields(category, answer, clue=clue, air_date=air_date),
            str(note_id),
        ),
    )
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, 1, 0, 1, 0, 2, 2, 100, ?, 2500, 3, 1, 0, 0, 0, 0, '')",
        (card_id, note_id, REVIEWED_INTERVAL_DAYS),
    )
    conn.execute(
        "INSERT INTO revlog VALUES (?, ?, 0, 3, ?, 7, 2500, 5000, 1)",
        (note_id * 1000, card_id, REVIEWED_INTERVAL_DAYS),
    )
