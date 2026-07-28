#!/usr/bin/env python3
"""Move genuinely dead cards out of the daily queue into an Archive subdeck.

Nothing is ever deleted. Cards land in 'Jeopardy Smart Prep::Archive', where
they stay searchable and studiable on demand but no longer compete for review
time. `--restore` puts every one of them back.

A card is archived when it is any of:

  malformed    the answer is empty/one character, or the clue is blank
  stale        time-anchored wording ("the current president") on an old clue,
               whose stored response may simply be wrong today
  one-off      the answer never repeats across 42 seasons, and the clue is old
  dead-topic   the answer has not appeared anywhere since ARCHIVE_DEAD_TOPIC_*
  duplicate    a near-verbatim restatement of another clue with the same answer
               (the most recent phrasing is kept)

Two exemptions always win: cards you have already reviewed, and anything aired
from ARCHIVE_PROTECT_YEAR onward (recent one-offs may yet recur).

Usage:
  python archive_dead_cards.py [--db PATH] [--dry-run] [--restore] [--no-backup]
"""

import argparse
import logging
import re
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

from jeopardy_consts import (
    ANKI_COLLECTION_PATH,
    ARCHIVE_DEAD_TOPIC_BEFORE_YEAR,
    ARCHIVE_ONEOFF_BEFORE_YEAR,
    ARCHIVE_PROTECT_YEAR,
    ARCHIVE_STALE_BEFORE_YEAR,
    DECK_ARCHIVE_NAME,
    DECK_TARGET_NAME,
    DUPLICATE_BLOCK_MAX_POSTINGS,
    DUPLICATE_JACCARD_THRESHOLD,
    FIELD_AIR_DATE,
    FIELD_ANSWER,
    FIELD_QUESTION,
    JEOPARDY_NOTETYPE_ID,
    USN_PENDING,
)
from jeopardy_db_helpers import (
    connect_anki,
    is_malformed_answer,
    merge_plural_variants,
    normalize_answer,
    require_anki_closed,
    strip_html_media,
)
from smart_prep import get_year_from_date

logger = logging.getLogger(__name__)

# Clues pinned to their broadcast moment; the stored response ages out.
#
# NOTE: "this year", "this month", "this season" and friends are deliberately
# ABSENT. In Jeopardy phrasing "this X" is the self-referential pointer to the
# thing being asked for ("the carnation is the flower for this month" wants the
# month named), not a reference to the year of broadcast. Including them matched
# tens of thousands of perfectly timeless clues.
_STALE_RE = re.compile(
    r"\b(current(ly)?(?! events)|present-day|nowadays|recent(ly)?|newest|latest"
    r"|as of \d{4}|so far this|to date|upcoming"
    r"|just (released|published|opened))\b|-elect\b",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^a-z0-9 ]+")
_ARCHIVE_TAG_PREFIX = "archived:"

# Function words carry no fact content, so they are excluded when judging whether
# two clues state the same thing.
_STOPWORDS: frozenset[str] = frozenset(
    """the a an of in on at to for this that these those is was were are be been it
    its he she his her they them with by from as and or but not you your we our us i
    who whom which what when where how why said says one two first also can may had
    has have do does did will would""".split()
)


class CardInfo:
    """Everything needed to judge one card. Slots keep 452K instances cheap."""

    __slots__ = (
        "card_id",
        "note_id",
        "year",
        "answer",
        "raw_answer",
        "tokens",
        "reviewed",
    )

    def __init__(
        self,
        card_id: int,
        note_id: int,
        year: int,
        answer: str,
        raw_answer: str,
        tokens: frozenset[str],
        reviewed: bool,
    ) -> None:
        self.card_id = card_id
        self.note_id = note_id
        self.year = year
        self.answer = answer
        self.raw_answer = raw_answer
        self.tokens = tokens
        self.reviewed = reviewed


def content_tokens(text: str) -> frozenset[str]:
    """Reduce clue text to its content words for similarity comparison."""
    lowered = _NON_WORD_RE.sub(" ", strip_html_media(text).lower())
    return frozenset(w for w in lowered.split() if w not in _STOPWORDS and len(w) > 2)


def load_cards(conn: sqlite3.Connection) -> tuple[list[CardInfo], dict[int, str]]:
    """Load every Jeopardy card plus the raw clue text keyed by card id."""
    reviewed_ids = {
        int(row[0])
        for row in conn.execute(
            """SELECT DISTINCT r.cid FROM revlog r
               JOIN cards c ON c.id = r.cid JOIN notes n ON n.id = c.nid
               WHERE n.mid = ?""",
            (JEOPARDY_NOTETYPE_ID,),
        )
    }
    cards: list[CardInfo] = []
    clues: dict[int, str] = {}
    for flds, card_id, note_id in conn.execute(
        "SELECT n.flds, c.id, n.id FROM notes n JOIN cards c ON c.nid = n.id WHERE n.mid = ?",
        (JEOPARDY_NOTETYPE_ID,),
    ):
        parts = flds.split("\x1f")
        if len(parts) < FIELD_ANSWER + 1:
            continue
        clue = strip_html_media(parts[FIELD_QUESTION]).strip()
        raw_answer = parts[FIELD_ANSWER]
        clues[int(card_id)] = clue
        cards.append(
            CardInfo(
                card_id=int(card_id),
                note_id=int(note_id),
                year=get_year_from_date(parts[FIELD_AIR_DATE].strip()),
                answer=normalize_answer(raw_answer),
                raw_answer=raw_answer,
                tokens=content_tokens(clue),
                reviewed=int(card_id) in reviewed_ids,
            )
        )
    logger.info("Loaded %d cards (%d already reviewed)", len(cards), len(reviewed_ids))
    return cards, clues


def find_duplicate_cards(cards: list[CardInfo]) -> set[int]:
    """Return card ids that restate another clue for the same answer.

    Clues are compared only against others sharing an answer, and within that
    group only against those sharing a content word, which keeps the pairwise
    work tractable across the full collection. The most recent card in each
    cluster is kept.
    """
    by_answer: dict[str, list[CardInfo]] = defaultdict(list)
    for card in cards:
        if card.answer:
            by_answer[card.answer].append(card)

    duplicates: set[int] = set()
    for group in by_answer.values():
        if len(group) < 2:
            continue
        postings: dict[str, list[int]] = defaultdict(list)
        for idx, card in enumerate(group):
            for token in card.tokens:
                postings[token].append(idx)

        parent = list(range(len(group)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        compared: set[tuple[int, int]] = set()
        for members in postings.values():
            if len(members) > DUPLICATE_BLOCK_MAX_POSTINGS:
                continue
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = sorted((members[i], members[j]))
                    if (a, b) in compared:
                        continue
                    compared.add((a, b))
                    ta, tb = group[a].tokens, group[b].tokens
                    if not ta or not tb:
                        continue
                    overlap = len(ta & tb)
                    if (
                        overlap
                        and overlap / len(ta | tb) >= DUPLICATE_JACCARD_THRESHOLD
                    ):
                        ra, rb = find(a), find(b)
                        if ra != rb:
                            parent[ra] = rb

        clusters: dict[int, list[int]] = defaultdict(list)
        for idx in range(len(group)):
            clusters[find(idx)].append(idx)
        for members in clusters.values():
            if len(members) < 2:
                continue
            # Keep the newest phrasing; archive the rest.
            members.sort(key=lambda i: group[i].year, reverse=True)
            duplicates.update(group[i].card_id for i in members[1:])
    return duplicates


def classify(cards: list[CardInfo], clues: dict[int, str]) -> dict[int, str]:
    """Return {card_id: archive_reason} for every card that should be archived."""
    answer_years: dict[str, list[int]] = defaultdict(list)
    for card in cards:
        if card.answer:
            answer_years[card.answer].append(card.year)
    folded = merge_plural_variants(dict(answer_years))
    last_seen = {a: max(y) for a, y in folded.items()}
    occurrences = {a: len(y) for a, y in folded.items()}

    def topic_key(answer: str) -> str:
        """Resolve an answer onto the key its plural variant folded into."""
        if answer in folded:
            return answer
        return answer[:-1] if answer.endswith("s") and answer[:-1] in folded else answer

    duplicates = find_duplicate_cards(cards)
    logger.info("Near-duplicate clues detected: %d", len(duplicates))

    reasons: dict[int, str] = {}
    for card in cards:
        # Exemptions always win.
        if card.reviewed or card.year >= ARCHIVE_PROTECT_YEAR:
            continue

        clue = clues.get(card.card_id, "")
        key = topic_key(card.answer)
        if is_malformed_answer(card.raw_answer) or not clue:
            reasons[card.card_id] = "malformed"
        elif card.year < ARCHIVE_STALE_BEFORE_YEAR and _STALE_RE.search(clue):
            reasons[card.card_id] = "stale"
        elif last_seen.get(key, 0) < ARCHIVE_DEAD_TOPIC_BEFORE_YEAR:
            reasons[card.card_id] = "dead-topic"
        elif occurrences.get(key, 0) == 1 and card.year < ARCHIVE_ONEOFF_BEFORE_YEAR:
            reasons[card.card_id] = "one-off"
        elif card.card_id in duplicates:
            reasons[card.card_id] = "duplicate"
    return reasons


def ensure_archive_deck(conn: sqlite3.Connection) -> int:
    """Return the Archive subdeck id, creating it from the parent's config if needed."""
    row = conn.execute(
        "SELECT id FROM decks WHERE name = ?", (DECK_ARCHIVE_NAME,)
    ).fetchone()
    if row:
        return int(row[0])

    parent = conn.execute(
        "SELECT common, kind FROM decks WHERE name = ?", (DECK_TARGET_NAME,)
    ).fetchone()
    if not parent:
        logger.error("Parent deck '%s' not found", DECK_TARGET_NAME)
        raise SystemExit(1)

    deck_id = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO decks (id, name, mtime_secs, usn, common, kind) VALUES (?, ?, ?, ?, ?, ?)",
        (
            deck_id,
            DECK_ARCHIVE_NAME,
            int(time.time()),
            USN_PENDING,
            parent[0],
            parent[1],
        ),
    )
    logger.info("Created deck '%s' (%d)", DECK_ARCHIVE_NAME, deck_id)
    return deck_id


def apply_archive(
    conn: sqlite3.Connection, reasons: dict[int, str], deck_id: int
) -> None:
    """Move classified cards into the archive deck and tag their notes."""
    now = int(time.time())
    conn.executemany(
        "UPDATE cards SET did = ?, mod = ?, usn = ? WHERE id = ?",
        [(deck_id, now, USN_PENDING, cid) for cid in reasons],
    )
    note_reason: dict[int, str] = {}
    for card_id, reason in reasons.items():
        row = conn.execute("SELECT nid FROM cards WHERE id = ?", (card_id,)).fetchone()
        if row:
            note_reason[int(row[0])] = reason
    updates: list[tuple[str, int]] = []
    for note_id, reason in note_reason.items():
        row = conn.execute("SELECT tags FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            continue
        kept = [t for t in row[0].split() if not t.startswith(_ARCHIVE_TAG_PREFIX)]
        kept.append(f"{_ARCHIVE_TAG_PREFIX}{reason}")
        updates.append((" " + " ".join(kept) + " ", note_id))
    conn.executemany(
        "UPDATE notes SET tags = ?, usn = ? WHERE id = ?",
        [(t, USN_PENDING, n) for t, n in updates],
    )


def restore_all(conn: sqlite3.Connection) -> int:
    """Move every archived card back to the main deck and drop archive tags."""
    row = conn.execute(
        "SELECT id FROM decks WHERE name = ?", (DECK_ARCHIVE_NAME,)
    ).fetchone()
    if not row:
        logger.info("No archive deck present — nothing to restore")
        return 0
    archive_id = int(row[0])
    target = conn.execute(
        "SELECT id FROM decks WHERE name = ?", (DECK_TARGET_NAME,)
    ).fetchone()
    if not target:
        logger.error("Target deck '%s' not found", DECK_TARGET_NAME)
        raise SystemExit(1)

    now = int(time.time())
    cursor = conn.execute(
        "UPDATE cards SET did = ?, mod = ?, usn = ? WHERE did = ?",
        (int(target[0]), now, USN_PENDING, archive_id),
    )
    moved = int(cursor.rowcount)
    for note_id, tags in conn.execute(
        "SELECT id, tags FROM notes WHERE tags LIKE ?", (f"%{_ARCHIVE_TAG_PREFIX}%",)
    ).fetchall():
        kept = [t for t in tags.split() if not t.startswith(_ARCHIVE_TAG_PREFIX)]
        new_tags = " " + " ".join(kept) + " " if kept else ""
        conn.execute(
            "UPDATE notes SET tags = ?, usn = ? WHERE id = ?",
            (new_tags, USN_PENDING, note_id),
        )
    return moved


def print_report(reasons: dict[int, str], total_cards: int) -> None:
    """Summarize what will be archived."""
    counts: dict[str, int] = defaultdict(int)
    for reason in reasons.values():
        counts[reason] += 1
    remaining = total_cards - len(reasons)
    print("\n=== ARCHIVE PLAN ===\n")
    print(f"  {'reason':<14}{'cards':>10}")
    print("  " + "-" * 24)
    for reason in ("malformed", "stale", "dead-topic", "one-off", "duplicate"):
        print(f"  {reason:<14}{counts[reason]:>10,}")
    print("  " + "-" * 24)
    print(
        f"  {'TOTAL':<14}{len(reasons):>10,}  ({len(reasons)/total_cards:.1%} of deck)"
    )
    print(f"\n  active deck after archiving: {remaining:,}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=ANKI_COLLECTION_PATH)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument(
        "--restore", action="store_true", help="move all archived cards back"
    )
    parser.add_argument("--no-backup", action="store_true")
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

    if args.restore:
        with conn:
            moved = restore_all(conn)
        conn.close()
        logger.info("✓ Restored %d cards to '%s'", moved, DECK_TARGET_NAME)
        return

    cards, clues = load_cards(conn)
    reasons = classify(cards, clues)
    print_report(reasons, len(cards))

    if args.dry_run:
        logger.info("Dry run — no changes written.")
        conn.close()
        return

    with conn:
        deck_id = ensure_archive_deck(conn)
        apply_archive(conn, reasons, deck_id)
        conn.execute("UPDATE col SET mod = ?", (int(time.time() * 1000),))
    conn.close()
    logger.info("✓ Archived %d cards to '%s'", len(reasons), DECK_ARCHIVE_NAME)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
