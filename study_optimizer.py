"""Optimize Anki study scheduling by tuning ease factors and adding performance tags.

Reads the live Anki collection DB, computes per-card and per-category accuracy from
revlog, then writes back: updated ease factors, perf tags on notes, and reordered new
card positions. Run with --analysis-only first to preview without writing.
"""

import argparse
import logging
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Final, TypedDict

from jeopardy_consts import (
    ANKI_COLLECTION_PATH,
    DECK_ARCHIVE_NAME,
    EASE_BASE,
    EASE_MAX,
    EASE_MIN,
    FIELD_AIR_DATE,
    FIELD_CATEGORY,
    FIELD_ROUND,
    FIELD_VALUE,
    FREQ_EASE_PENALTY,
    JEOPARDY_NOTETYPE_ID,
    MIN_REVIEWS_FOR_CARD_PERF,
    PERF_STRONG_THRESHOLD,
    PERF_WEAK_THRESHOLD,
    PRIOR_ACCURACY,
    PRIOR_WEIGHT,
    ROUND_FINAL_JEOPARDY,
    TOTAL_FIELDS,
    WEAKNESS_EASE_PENALTY,
    WEAKNESS_PRIORITY_BOOST,
)
from jeopardy_db_helpers import connect_anki, strip_html_media

logger = logging.getLogger(__name__)

_FREQ_SCORE_RE = re.compile(r'class="fq [hmlr]">(\d+)<')
_VALUE_RE = re.compile(r"(\d[\d,]*)")
_TIER_SCORE_FALLBACK: Final[dict[str, int]] = {
    "high": 75,
    "medium": 55,
    "low": 30,
    "rare": 7,
}

_FREQ_TIER_WEIGHT: Final[dict[str, float]] = {
    "high": 4.0,
    "medium": 3.0,
    "low": 2.0,
    "rare": 1.0,
}
_PERF_PREFIXES: Final[tuple[str, ...]] = ("perf:", "perfsubcat:", "perfsubject:")
_REPORT_TOP_N: Final[int] = 20


class CardRecord(TypedDict):
    note_id: int
    card_id: int
    freq_tier: str
    freq_score: int
    category: str
    air_date: str
    round_name: str
    value_rank: int
    subcat: str
    subject: str
    card_type: int
    due: int
    factor: int
    reviews: int
    correct: int
    raw_tags: str
    archived: bool


class CategoryStats(TypedDict):
    total_reviewed: int
    blended_accuracy: float
    avg_freq_weight: float


def _parse_tags(tags: str) -> tuple[str, str, str]:
    """Return (freq_tier, subcat, subject) parsed from Anki space-delimited tags."""
    freq_tier = "low"
    subcat = ""
    subject = ""
    for tag in tags.split():
        if tag.startswith("freq:"):
            freq_tier = tag[5:]
        elif tag.startswith("subcat:") and not tag.startswith("subcat2:"):
            subcat = tag[7:]
        elif tag.startswith("subject:"):
            subject = tag[8:]
    return freq_tier, subcat, subject


def _informative_perf_tags(card_perf: str, subcat_perf: str) -> list[str]:
    """Return only the perf tags that carry information worth storing.

    These tags land on every one of ~452K notes, so the default states are not
    written: `perf:new` covers 99.8% of the deck and `perfsubcat:strong` marks
    the areas you have no reason to search for. Persisting both cost roughly
    20MB and helped push the collection past AnkiWeb's 300MB upload ceiling.
    Their absence is exactly as meaningful as their presence.
    """
    tags: list[str] = []
    if card_perf != "new":
        tags.append(f"perf:{card_perf}")
    if subcat_perf != "strong":
        tags.append(f"perfsubcat:{subcat_perf}")
    return tags


def _strip_perf_tags(tags: str) -> list[str]:
    """Return tag list with all perf:*, perfsubcat:*, perfsubject:* removed."""
    return [t for t in tags.split() if not any(t.startswith(p) for p in _PERF_PREFIXES)]


def parse_card_value(value_str: str) -> int:
    """Extract the integer dollar amount from a Value field, for in-group sorting.

    Two source formats are mixed in this collection: the legacy pre-2019 deck
    writes daily doubles as "DD: $1,000" (comma thousands, "DD: " prefix), while
    the jwolle1 post-2019 import writes plain "$1600" (no comma) for every clue
    including daily doubles. A regex search over the digits handles both
    uniformly rather than special-casing the prefix.

    Returns -1 (sorts last under a descending sort) for blank or malformed
    values (Final Jeopardy has no fixed value; a handful of legacy rows have
    junk like "()").
    """
    m = _VALUE_RE.search(strip_html_media(value_str))
    return int(m.group(1).replace(",", "")) if m else -1


def load_card_stats(conn: sqlite3.Connection) -> dict[int, CardRecord]:
    """Load all Jeopardy cards with scheduling and note tag data."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT n.id, c.id, n.tags, c.type, c.due, c.factor, n.flds,
               COALESCE(d.name, '')
        FROM notes n
        JOIN cards c ON c.nid = n.id
        LEFT JOIN decks d ON d.id = c.did
        WHERE n.mid = ?
        """,
        (JEOPARDY_NOTETYPE_ID,),
    )
    cards: dict[int, CardRecord] = {}
    for (
        note_id,
        card_id,
        tags,
        card_type,
        due,
        factor,
        flds,
        deck_name,
    ) in cursor.fetchall():
        freq_tier, subcat, subject = _parse_tags(tags)
        parts = flds.split("\x1f")
        # air_date is populated on every note, unlike Show number (blank for
        # ~18% of the deck — see the grouping comment in main()), so it's
        # read here for use as the day-grouping key.
        air_date = parts[FIELD_AIR_DATE].strip() if len(parts) > FIELD_AIR_DATE else ""
        round_name = parts[FIELD_ROUND].strip() if len(parts) > FIELD_ROUND else ""
        category = parts[FIELD_CATEGORY].strip() if len(parts) > FIELD_CATEGORY else ""
        value_rank = parse_card_value(
            parts[FIELD_VALUE] if len(parts) > FIELD_VALUE else ""
        )
        freq_score = _TIER_SCORE_FALLBACK.get(freq_tier, 30)
        if len(parts) > TOTAL_FIELDS:
            m = _FREQ_SCORE_RE.search(parts[TOTAL_FIELDS])
            if m:
                freq_score = int(m.group(1))
        cards[card_id] = {
            "note_id": note_id,
            "card_id": card_id,
            "freq_tier": freq_tier,
            "freq_score": freq_score,
            "category": category,
            "air_date": air_date,
            "round_name": round_name,
            "value_rank": value_rank,
            "subcat": subcat,
            "subject": subject,
            "card_type": card_type,
            "due": due,
            "factor": factor,
            "reviews": 0,
            "correct": 0,
            "raw_tags": tags,
            "archived": deck_name == DECK_ARCHIVE_NAME,
        }
    logger.info("Loaded %d Jeopardy cards", len(cards))
    return cards


def load_revlog(conn: sqlite3.Connection) -> dict[int, tuple[int, int]]:
    """Return {card_id: (correct_count, total_count)} from revlog for Jeopardy cards."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT r.cid,
               SUM(CASE WHEN r.ease >= 3 THEN 1 ELSE 0 END),
               COUNT(*)
        FROM revlog r
        INNER JOIN cards c ON c.id = r.cid
        INNER JOIN notes n ON n.id = c.nid
        WHERE n.mid = ?
        GROUP BY r.cid
        """,
        (JEOPARDY_NOTETYPE_ID,),
    )
    return {int(row[0]): (int(row[1]), int(row[2])) for row in cursor.fetchall()}


def merge_revlog(
    cards: dict[int, CardRecord], revlog: dict[int, tuple[int, int]]
) -> None:
    """Mutate cards in-place, filling reviews and correct from revlog."""
    for card_id, (correct, total) in revlog.items():
        if card_id in cards:
            cards[card_id]["correct"] = correct
            cards[card_id]["reviews"] = total


def compute_category_stats(
    cards: dict[int, CardRecord],
) -> tuple[dict[str, CategoryStats], dict[str, CategoryStats]]:
    """Return (subcat_stats, subject_stats) with Bayesian-blended accuracy per category."""
    subcat_correct: dict[str, float] = defaultdict(float)
    subcat_total: dict[str, int] = defaultdict(int)
    subcat_freq_sum: dict[str, float] = defaultdict(float)
    subcat_count: dict[str, int] = defaultdict(int)

    subject_correct: dict[str, float] = defaultdict(float)
    subject_total: dict[str, int] = defaultdict(int)
    subject_freq_sum: dict[str, float] = defaultdict(float)
    subject_count: dict[str, int] = defaultdict(int)

    for rec in cards.values():
        sc = rec["subcat"] or "Unknown"
        subj = rec["subject"] or "Unknown"
        fw = _FREQ_TIER_WEIGHT.get(rec["freq_tier"], 2.0)

        subcat_correct[sc] += rec["correct"]
        subcat_total[sc] += rec["reviews"]
        subcat_freq_sum[sc] += fw
        subcat_count[sc] += 1

        subject_correct[subj] += rec["correct"]
        subject_total[subj] += rec["reviews"]
        subject_freq_sum[subj] += fw
        subject_count[subj] += 1

    def _blend(correct: float, total: int) -> float:
        return (PRIOR_ACCURACY * PRIOR_WEIGHT + correct) / (PRIOR_WEIGHT + total)

    subcat_stats: dict[str, CategoryStats] = {
        sc: {
            "total_reviewed": subcat_total[sc],
            "blended_accuracy": _blend(subcat_correct[sc], subcat_total[sc]),
            "avg_freq_weight": subcat_freq_sum[sc] / max(subcat_count[sc], 1),
        }
        for sc in subcat_count
    }
    subject_stats: dict[str, CategoryStats] = {
        subj: {
            "total_reviewed": subject_total[subj],
            "blended_accuracy": _blend(subject_correct[subj], subject_total[subj]),
            "avg_freq_weight": subject_freq_sum[subj] / max(subject_count[subj], 1),
        }
        for subj in subject_count
    }
    return subcat_stats, subject_stats


def accuracy_to_tier(accuracy: float) -> str:
    """Map accuracy to 'weak', 'medium', or 'strong'."""
    if accuracy >= PERF_STRONG_THRESHOLD:
        return "strong"
    if accuracy >= PERF_WEAK_THRESHOLD:
        return "medium"
    return "weak"


def study_priority(freq_score: int, subcat_accuracy: float) -> float:
    """Rank a card for the new-card queue: game frequency, tilted toward weak areas.

    The frequency score stays a pure "how likely is this to be asked" measure, so
    the personal-performance adjustment is applied here at ordering time instead.
    A subcategory answered at 60% is surfaced ahead of an equally frequent one
    answered at 95%.
    """
    return freq_score * (1.0 + WEAKNESS_PRIORITY_BOOST * (1.0 - subcat_accuracy))


def interleave_blocks(
    main: list[list[int]], sparse: list[list[int]]
) -> list[list[int]]:
    """Spread `sparse` blocks evenly across `main`, preserving each side's own order.

    Used to intersperse Final Jeopardy cards (each a 1-card `sparse` block) among
    the day-category groups (each a `main` block of up to 5 card IDs, already
    value-sorted) at a roughly constant cadence — "every once in a while" rather
    than wherever they happen to fall when everything is ranked by raw score
    (Final Jeopardy's 4x stake multiplier would otherwise cluster them all at
    the front of the queue).

    A block is a `list[int]` of card IDs meant to be shown consecutively; both
    `main` and `sparse` are lists of such blocks. Neither list is re-sorted —
    ordering within and across blocks is decided by the caller.
    """
    if not sparse:
        return list(main)
    if not main:
        return list(sparse)
    result: list[list[int]] = []
    step = len(main) / len(sparse)
    next_threshold = step
    sparse_idx = 0
    for i, block in enumerate(main, start=1):
        result.append(block)
        while sparse_idx < len(sparse) and i >= next_threshold:
            result.append(sparse[sparse_idx])
            sparse_idx += 1
            next_threshold += step
    # Floating-point rounding can leave the last sparse item(s) unplaced.
    result.extend(sparse[sparse_idx:])
    return result


def compute_ease(freq_tier: str, weakness_tier: str) -> int:
    """Compute ease factor from freq and weakness tiers, clamped to [EASE_MIN, EASE_MAX]."""
    freq_penalty = FREQ_EASE_PENALTY.get(freq_tier, 0)
    weak_penalty = WEAKNESS_EASE_PENALTY.get(weakness_tier, 0)
    return max(EASE_MIN, min(EASE_MAX, EASE_BASE - freq_penalty - weak_penalty))


def apply_updates(
    conn: sqlite3.Connection,
    ease_updates: list[tuple[int, int]],
    tag_updates: list[tuple[str, int]],
    due_updates: list[tuple[int, int]],
) -> None:
    """Write ease, tag, and due updates in a single transaction."""
    with conn:
        conn.executemany("UPDATE cards SET factor = ? WHERE id = ?", ease_updates)
        conn.executemany("UPDATE notes SET tags = ? WHERE id = ?", tag_updates)
        conn.executemany("UPDATE cards SET due = ? WHERE id = ?", due_updates)


def print_report(
    subcat_stats: dict[str, CategoryStats],
    subject_stats: dict[str, CategoryStats],
    ease_deltas: list[int],
    perf_counts: dict[str, int],
) -> None:
    """Print study optimization report to stdout."""
    print("\n=== STUDY OPTIMIZER REPORT ===\n")

    # Top weakest subcats by urgency (high freq × low accuracy)
    urgency: dict[str, float] = {
        sc: (1.0 - stats["blended_accuracy"]) * stats["avg_freq_weight"]
        for sc, stats in subcat_stats.items()
        if stats["total_reviewed"] > 0
    }
    top_weak = sorted(urgency, key=lambda sc: urgency[sc], reverse=True)[:_REPORT_TOP_N]
    print(
        f"TOP {_REPORT_TOP_N} WEAKEST SUBCATEGORIES (urgency = weakness × freq weight):"
    )
    print(f"  {'Subcat':<42} {'Accuracy':>9} {'FreqWt':>7} {'Reviews':>9}")
    print("  " + "-" * 70)
    for sc in top_weak:
        s = subcat_stats[sc]
        print(
            f"  {sc:<42} {s['blended_accuracy']:>8.1%} {s['avg_freq_weight']:>7.1f} {s['total_reviewed']:>9}"
        )

    print()

    # High-freq strong subcats that need maintenance
    maintain: dict[str, CategoryStats] = {
        sc: stats
        for sc, stats in subcat_stats.items()
        if stats["avg_freq_weight"] >= _FREQ_TIER_WEIGHT["medium"]
        and accuracy_to_tier(stats["blended_accuracy"]) == "strong"
    }
    top_maintain = sorted(
        maintain, key=lambda sc: maintain[sc]["avg_freq_weight"], reverse=True
    )[:_REPORT_TOP_N]
    print(f"TOP {_REPORT_TOP_N} HIGH-FREQ STRONG SUBCATS (maintain — don't let slip):")
    print(f"  {'Subcat':<42} {'Accuracy':>9} {'FreqWt':>7} {'Reviews':>9}")
    print("  " + "-" * 70)
    for sc in top_maintain:
        s = subcat_stats[sc]
        print(
            f"  {sc:<42} {s['blended_accuracy']:>8.1%} {s['avg_freq_weight']:>7.1f} {s['total_reviewed']:>9}"
        )

    print()

    # Subject-level summary
    print("SUBJECT ACCURACY SUMMARY:")
    for subj in sorted(
        subject_stats, key=lambda s: subject_stats[s]["blended_accuracy"]
    ):
        s = subject_stats[subj]
        tier = accuracy_to_tier(s["blended_accuracy"])
        print(
            f"  {subj:<30} {s['blended_accuracy']:>8.1%}  [{tier}]  reviews={s['total_reviewed']}"
        )

    print()

    # Ease delta histogram
    print("EASE DELTA DISTRIBUTION (new ease - old ease):")
    buckets: dict[str, int] = {
        "<= -500": 0,
        "-499 to -200": 0,
        "-199 to -100": 0,
        "-99 to -1": 0,
        "0 (unchanged)": 0,
        "+1 to +99": 0,
        "+100 to +200": 0,
        "> +200": 0,
    }
    for delta in ease_deltas:
        if delta <= -500:
            buckets["<= -500"] += 1
        elif delta <= -200:
            buckets["-499 to -200"] += 1
        elif delta <= -100:
            buckets["-199 to -100"] += 1
        elif delta < 0:
            buckets["-99 to -1"] += 1
        elif delta == 0:
            buckets["0 (unchanged)"] += 1
        elif delta < 100:
            buckets["+1 to +99"] += 1
        elif delta <= 200:
            buckets["+100 to +200"] += 1
        else:
            buckets["> +200"] += 1
    for label, count in buckets.items():
        print(f"  {label:<20}: {count:>8}")

    print()

    # Perf tag distribution
    total_cards = sum(perf_counts.values())
    print(f"PERFORMANCE TAG DISTRIBUTION (card-level, total={total_cards}):")
    for tier in ("new", "weak", "medium", "strong"):
        count = perf_counts.get(tier, 0)
        pct = count / total_cards * 100 if total_cards > 0 else 0.0
        print(f"  perf:{tier:<8}: {count:>8}  ({pct:>5.1f}%)")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optimize Anki study scheduling for Jeopardy cards."
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=ANKI_COLLECTION_PATH,
        help="Path to collection.anki2 (default: %(default)s)",
    )
    parser.add_argument(
        "--analysis-only",
        action="store_true",
        help="Print report without writing any changes.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip database backup before writing (not recommended).",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        sys.exit(1)

    wal_path = db_path.with_name(db_path.name + "-wal")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        logger.error(
            "Anki appears to be running (WAL file present: %s). Close Anki first.",
            wal_path,
        )
        sys.exit(1)

    if not args.analysis_only and not args.no_backup:
        backup_path = db_path.with_name(db_path.name + ".bak")
        shutil.copy2(db_path, backup_path)
        logger.info("Backed up database to %s", backup_path)

    conn = connect_anki(db_path)

    cards = load_card_stats(conn)
    revlog = load_revlog(conn)
    merge_revlog(cards, revlog)

    subcat_stats, subject_stats = compute_category_stats(cards)

    ease_updates: list[tuple[int, int]] = []
    tag_updates_by_note: dict[int, str] = {}
    ease_deltas: list[int] = []
    perf_counts: dict[str, int] = defaultdict(int)
    cat_groups: dict[tuple[str, str, str], list[tuple[float, int]]] = defaultdict(list)
    fj_cards: list[tuple[float, int]] = []

    for rec in cards.values():
        note_id = rec["note_id"]
        card_id = rec["card_id"]
        sc = rec["subcat"] or "Unknown"

        if rec["reviews"] >= MIN_REVIEWS_FOR_CARD_PERF:
            card_acc = rec["correct"] / rec["reviews"]
            card_perf = accuracy_to_tier(card_acc)
        else:
            card_perf = "new"
        perf_counts[card_perf] += 1

        subcat_acc = subcat_stats[sc]["blended_accuracy"]
        subcat_perf = accuracy_to_tier(subcat_acc)

        new_ease = compute_ease(rec["freq_tier"], subcat_perf)
        ease_updates.append((new_ease, card_id))
        ease_deltas.append(new_ease - rec["factor"])

        if note_id not in tag_updates_by_note:
            base = _strip_perf_tags(rec["raw_tags"])
            perf_tags = _informative_perf_tags(card_perf, subcat_perf)
            had_perf_tags = base != rec["raw_tags"].split()
            if perf_tags or had_perf_tags:
                merged = base + perf_tags
                tag_updates_by_note[note_id] = (
                    " " + " ".join(merged) + " " if merged else ""
                )

        if rec["card_type"] == 0 and not rec["archived"]:
            priority = study_priority(rec["freq_score"], subcat_acc)
            if rec["round_name"] == ROUND_FINAL_JEOPARDY:
                # No grouping partner (one clue/day), and its 4x stake
                # multiplier would otherwise cluster every FJ card at the
                # front of the queue if ranked in with everything else by raw
                # score. Held out and interspersed evenly below instead.
                fj_cards.append((priority, card_id))
            else:
                # Grouped by air_date rather than Show number: the post-2019
                # jwolle1 import (see update_collection.py) never populates
                # Show number, so that field is blank for ~18% of the deck —
                # every blank-Show-number card sharing a reused category name
                # (e.g. "AMERICAN HISTORY") collapsed into one giant group
                # spanning years of games. air_date is populated on every
                # note. round_name is included too, to keep the (rare) case
                # of the same category name reused in both rounds on the same
                # day from merging into a single 10-card group.
                group_key = (rec["air_date"], rec["round_name"], rec["category"])
                cat_groups[group_key].append((priority, card_id))

    # Order each day's category group by descending dollar value ($1000 ->
    # $800 -> ...), tie-broken by study priority then card ID for determinism.
    # (compute_stake_multiplier already used this hierarchy to weight the
    # frequency score — this is purely presentation order within the group.)
    scored_groups: list[tuple[float, list[int]]] = []
    for card_entries in cat_groups.values():
        ordered = sorted(
            card_entries,
            key=lambda e: (-cards[e[1]]["value_rank"], -e[0], e[1]),
        )
        block = [card_id for _score, card_id in ordered]
        scored_groups.append((sum(score for score, _ in card_entries), block))

    # Sort the groups themselves by total freq score (highest first) — same
    # weakness-weighted priority as before, now decoupled from within-group
    # (value) order.
    sorted_groups = [
        block
        for _total, block in sorted(scored_groups, key=lambda x: x[0], reverse=True)
    ]

    # Rank Final Jeopardy cards among themselves the same way, then spread
    # them evenly across the whole queue so they show up every once in a
    # while rather than all at once.
    fj_blocks = [
        [card_id] for _score, card_id in sorted(fj_cards, key=lambda e: (-e[0], e[1]))
    ]
    merged_blocks = interleave_blocks(sorted_groups, fj_blocks)

    due_updates: list[tuple[int, int]] = []
    pos = 1
    for block in merged_blocks:
        for card_id in block:
            due_updates.append((pos, card_id))
            pos += 1
    tag_updates: list[tuple[str, int]] = [
        (tags, nid) for nid, tags in tag_updates_by_note.items()
    ]

    print_report(subcat_stats, subject_stats, ease_deltas, dict(perf_counts))

    if args.analysis_only:
        logger.info("Analysis-only mode — no changes written.")
        conn.close()
        return

    apply_updates(conn, ease_updates, tag_updates, due_updates)
    conn.close()
    logger.info(
        "Done: %d ease updates, %d tag updates, %d due reorders",
        len(ease_updates),
        len(tag_updates),
        len(due_updates),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
