#!/usr/bin/env python3
"""Jeopardy Smart Prep — blended frequency scoring, tagging, and on-card display.

For a collection that already spans 1984–2025 (see update_collection.py) and a
category taxonomy (see classify_categories.py), this:

  1. Computes a recency-weighted frequency for each card's exact ANSWER, its
     SUB-CATEGORY, and its broad SUBJECT.
  2. Blends those three (as percentiles) into a single 0-100 frequency score and
     a tier (freq:high/medium/low/rare).
  3. Adds a "Frequency Score" field to the Jeopardy note type + renders it on the
     card as a colored badge, and tags each note (freq:/subject:/subcat:/era:).

Usage:
  python smart_prep.py SOURCE.colpkg OUTPUT.colpkg
      [--taxonomy category_taxonomy.json] [--analysis-only]
"""

import argparse
import bisect
import json
import logging
import re
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from jeopardy_consts import (
    ERA_MODERN_START,
    ERA_RECENT_START,
    FIELD_AIR_DATE,
    FIELD_ANSWER,
    FIELD_CATEGORY,
    FREQ_FIELD_CONFIG_HEX,
    FREQ_FIELD_NAME,
    JEOPARDY_NOTETYPE_ID,
    RECENCY_WEIGHTS,
    SUBJECT_OTHER,
    TIER_BADGE_COLORS,
    TIER_HIGH_MIN,
    TIER_LOW_MIN,
    TIER_MEDIUM_MIN,
    TOTAL_FIELDS,
    WEIGHT_ANSWER,
    WEIGHT_SUBCATEGORY,
    WEIGHT_SUBJECT,
)
from jeopardy_db_helpers import (
    connect_anki,
    extract_colpkg,
    protobuf_prepend_to_field1,
    repack_colpkg,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger(__name__)

Tier = Literal["high", "medium", "low", "rare"]

# Per-note computed metadata used for scoring then writing.
# (answer_key, subcat_key, subject, subcat_label, secondary_subject, year)
NoteMeta = tuple[str, str, str, str, str, int]

_DEFAULT_SUBCAT = "Miscellaneous"
_TAG_SANITIZE_RE = re.compile(r"[^A-Za-z0-9]+")


def get_year_from_date(air_date: str) -> int:
    """Extract the year from a YYYY-MM-DD string (0 if invalid)."""
    try:
        return int(air_date.split("-")[0])
    except (ValueError, IndexError):
        return 0


def recency_weight(year: int) -> float:
    """Recency weight for a year (0.0 if outside the known range)."""
    return RECENCY_WEIGHTS.get(year, 0.0)


def get_era_tag(year: int) -> str:
    """Era tag for a year: era:recent / era:modern / era:old."""
    if year >= ERA_RECENT_START:
        return "era:recent"
    if year >= ERA_MODERN_START:
        return "era:modern"
    return "era:old"


def sanitize_tag_value(value: str) -> str:
    """Make a string safe to embed in an Anki tag (no spaces, no '::')."""
    return _TAG_SANITIZE_RE.sub("_", value).strip("_") or "Unknown"


def tier_from_score(score: float) -> Tier:
    """Map a 0-100 blended score to a frequency tier."""
    if score >= TIER_HIGH_MIN:
        return "high"
    if score >= TIER_MEDIUM_MIN:
        return "medium"
    if score >= TIER_LOW_MIN:
        return "low"
    return "rare"


def load_taxonomy(path: Path) -> dict[str, tuple[str, str, str]]:
    """Load the category taxonomy as {CATEGORY_UPPER: (subject, sub_category, secondary_subject)}.

    Args:
        path: Path to category_taxonomy.json

    Returns:
        Mapping of uppercased category -> (subject, sub_category, secondary_subject). Empty if the
        file is absent (every card then falls back to Other/Miscellaneous/"").
    """
    if not path.exists():
        logger.warning(f"Taxonomy {path} not found — all cards will be 'Other'")
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out: dict[str, tuple[str, str, str]] = {}
    for cat, info in raw.items():
        subject = str(info.get("subject", SUBJECT_OTHER)) or SUBJECT_OTHER
        sub_category = str(info.get("sub_category", _DEFAULT_SUBCAT)) or _DEFAULT_SUBCAT
        secondary_subject = str(info.get("secondary_subject", "")).strip()
        out[cat] = (subject, sub_category, secondary_subject)
    return out


def read_note_meta(
    conn: sqlite3.Connection, taxonomy: dict[str, tuple[str, str, str]]
) -> dict[int, NoteMeta]:
    """Read every Jeopardy note and derive its scoring metadata.

    Args:
        conn: SQLite connection
        taxonomy: category -> (subject, sub_category, secondary_subject) map

    Returns:
        note_id -> NoteMeta
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id, flds FROM notes WHERE mid = ?", (JEOPARDY_NOTETYPE_ID,))
    meta: dict[int, NoteMeta] = {}
    for note_id, flds in cursor.fetchall():
        parts = flds.split("\x1f")
        if len(parts) < TOTAL_FIELDS:
            continue
        answer = parts[FIELD_ANSWER].strip()
        category = parts[FIELD_CATEGORY].strip().upper()
        year = get_year_from_date(parts[FIELD_AIR_DATE].strip())
        subject, subcat_label, secondary_subject = taxonomy.get(
            category, (SUBJECT_OTHER, _DEFAULT_SUBCAT, "")
        )
        answer_key = answer.casefold()
        subcat_key = subcat_label.casefold()
        meta[note_id] = (
            answer_key,
            subcat_key,
            subject,
            subcat_label,
            secondary_subject,
            year,
        )
    logger.info(f"Read metadata for {len(meta)} notes")
    return meta


def build_frequency_tables(
    meta: dict[int, NoteMeta],
) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, float]]:
    """Build recency-weighted frequency sums for answer, sub-category, subject, secondary_subject.

    Args:
        meta: note_id -> NoteMeta

    Returns:
        (answer_score, subcat_score, subject_score, secondary_subject_score) keyed by the
        respective keys
    """
    answer_score: dict[str, float] = defaultdict(float)
    subcat_score: dict[str, float] = defaultdict(float)
    subject_score: dict[str, float] = defaultdict(float)
    secondary_subject_score: dict[str, float] = defaultdict(float)
    for (
        answer_key,
        subcat_key,
        subject,
        _label,
        secondary_subject,
        year,
    ) in meta.values():
        weight = recency_weight(year)
        if answer_key:
            answer_score[answer_key] += weight
        subcat_score[subcat_key] += weight
        subject_score[subject] += weight
        if secondary_subject:
            secondary_subject_score[secondary_subject] += weight
    return (
        dict(answer_score),
        dict(subcat_score),
        dict(subject_score),
        dict(secondary_subject_score),
    )


def make_percentile_fn(values: list[float]) -> Callable[[float], float]:
    """Return a function mapping a value to its 0-1 percentile within `values`.

    Uses an EXCLUSIVE rank (fraction of values strictly less than `value`) via
    bisect_left, so the large mass of zero-frequency cards (no recurring topic)
    maps to ~0 rather than being inflated to the top of the zero tie-group.
    """
    arr = sorted(values)
    n = len(arr)

    def percentile(value: float) -> float:
        if n == 0:
            return 0.0
        return bisect.bisect_left(arr, value) / n

    return percentile


def score_notes(
    meta: dict[int, NoteMeta],
    answer_score: dict[str, float],
    subcat_score: dict[str, float],
    subject_score: dict[str, float],
    secondary_subject_score: dict[str, float],
) -> dict[int, tuple[int, Tier]]:
    """Compute the blended 0-100 score and tier for every note.

    Each component is converted to a percentile across all notes, then blended
    with the configured weights and scaled to 0-100.

    For the subject component, uses max(primary_subject, secondary_subject) so that
    a wordplay category embedding a knowledge domain (e.g. "SCIENCE BEFORE & AFTER")
    gets credit for whichever domain scores higher.

    Args:
        meta: note_id -> NoteMeta
        answer_score: answer_key -> recency-weighted frequency
        subcat_score: subcat_key -> recency-weighted frequency
        subject_score: subject -> recency-weighted frequency
        secondary_subject_score: secondary_subject -> recency-weighted frequency

    Returns:
        note_id -> (score 0-100, tier)
    """
    # Per-note component raw values. "Other" subject and "Miscellaneous"
    # sub-category are the ABSENCE of a topic (grab-bag/unclassified), so they
    # earn no topic-frequency credit — their components are zeroed and the card
    # is scored on its exact-answer frequency alone.
    av: dict[int, float] = {}
    cv: dict[int, float] = {}
    sv: dict[int, float] = {}
    for nid, m in meta.items():
        answer_key, subcat_key, subject, subcat_label, secondary_subject, _year = m
        av[nid] = answer_score.get(answer_key, 0.0) if answer_key else 0.0
        cv[nid] = (
            0.0
            if subcat_label == _DEFAULT_SUBCAT
            else subcat_score.get(subcat_key, 0.0)
        )
        primary_sv = (
            0.0 if subject == SUBJECT_OTHER else subject_score.get(subject, 0.0)
        )
        secondary_sv = (
            secondary_subject_score.get(secondary_subject, 0.0)
            if secondary_subject
            else 0.0
        )
        sv[nid] = max(primary_sv, secondary_sv)

    pct_a = make_percentile_fn(list(av.values()))
    pct_c = make_percentile_fn(list(cv.values()))
    pct_s = make_percentile_fn(list(sv.values()))

    scored: dict[int, tuple[int, Tier]] = {}
    for nid in meta:
        blended = 100.0 * (
            WEIGHT_ANSWER * pct_a(av[nid])
            + WEIGHT_SUBCATEGORY * pct_c(cv[nid])
            + WEIGHT_SUBJECT * pct_s(sv[nid])
        )
        score = int(round(blended))
        scored[nid] = (score, tier_from_score(blended))
    return scored


def badge_html(score: int, tier: Tier, subject: str) -> str:
    """Render the on-card frequency badge as inline-styled HTML."""
    color = TIER_BADGE_COLORS.get(tier, "#566573")
    return (
        '<div style="display:inline-block;margin:4px 0;padding:2px 10px;'
        f"border-radius:12px;background:{color};color:#fff;font-size:12px;"
        f'font-weight:bold;">freq {score} · {tier} · {subject}</div>'
    )


def get_jeopardy_field_count(conn: sqlite3.Connection) -> int:
    """Return the current number of fields on the Jeopardy note type."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM fields WHERE ntid = ?", (JEOPARDY_NOTETYPE_ID,)
    )
    return int(cursor.fetchone()[0])


def add_frequency_field_and_template(conn: sqlite3.Connection) -> bool:
    """Add the Frequency Score field + template reference if not already present.

    Idempotent: if the field already exists, does nothing and reports that the
    notes already carry the extra field segment.

    Args:
        conn: SQLite connection

    Returns:
        True if the field was newly added (notes need the segment APPENDED);
        False if it already existed (notes' last segment should be REPLACED).
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT ord, name FROM fields WHERE ntid = ? ORDER BY ord",
        (JEOPARDY_NOTETYPE_ID,),
    )
    rows = cursor.fetchall()
    existing_names = [name for _ord, name in rows]
    if FREQ_FIELD_NAME in existing_names:
        logger.info(f"Field '{FREQ_FIELD_NAME}' already present — replace mode")
        return False

    new_ord = len(rows)
    config = bytes.fromhex(FREQ_FIELD_CONFIG_HEX)
    cursor.execute(
        "INSERT INTO fields (ntid, ord, name, config) VALUES (?, ?, ?, ?)",
        (JEOPARDY_NOTETYPE_ID, new_ord, FREQ_FIELD_NAME, config),
    )

    # Inject the field reference into the front template (protobuf field 1).
    cursor.execute(
        "SELECT ord, config FROM templates WHERE ntid = ?", (JEOPARDY_NOTETYPE_ID,)
    )
    for tmpl_ord, tconfig in cursor.fetchall():
        if b"Frequency Score" in tconfig:
            continue
        prefix = "{{#Frequency Score}}{{Frequency Score}}{{/Frequency Score}}\n"
        new_config = protobuf_prepend_to_field1(tconfig, prefix)
        cursor.execute(
            "UPDATE templates SET config = ?, mtime_secs = ?, usn = -1 "
            "WHERE ntid = ? AND ord = ?",
            (new_config, int(time.time()), JEOPARDY_NOTETYPE_ID, tmpl_ord),
        )

    # Bump the note type's mtime and the collection schema-modification time so
    # Anki recognizes the schema change on import.
    now_secs = int(time.time())
    now_ms = now_secs * 1000
    cursor.execute(
        "UPDATE notetypes SET mtime_secs = ?, usn = -1 WHERE id = ?",
        (now_secs, JEOPARDY_NOTETYPE_ID),
    )
    cursor.execute("UPDATE col SET scm = ?, mod = ?", (now_ms, now_ms))
    logger.info(f"Added '{FREQ_FIELD_NAME}' field (ord {new_ord}) + template badge")
    return True


def apply_scores_and_tags(
    conn: sqlite3.Connection,
    meta: dict[int, NoteMeta],
    scored: dict[int, tuple[int, Tier]],
    appended: bool,
) -> int:
    """Write the badge field + freq/subject/subcat/era tags onto every note.

    Args:
        conn: SQLite connection
        meta: note_id -> NoteMeta
        scored: note_id -> (score, tier)
        appended: True if the field was newly added (append the segment);
            False if replacing the existing last segment

    Returns:
        Number of notes updated
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, flds, tags FROM notes WHERE mid = ?", (JEOPARDY_NOTETYPE_ID,)
    )
    updates: list[tuple[str, str, int]] = []
    for note_id, flds, tags in cursor.fetchall():
        if note_id not in scored:
            continue
        score, tier = scored[note_id]
        _ak, _ck, subject, subcat_label, secondary_subject, year = meta[note_id]
        badge = badge_html(score, tier, subject)

        parts = flds.split("\x1f")
        if appended:
            parts.append(badge)
        else:
            parts[-1] = badge
        new_flds = "\x1f".join(parts)

        # Rebuild tags: drop any prior smart-prep tags, then add fresh ones.
        kept = [
            t
            for t in tags.split()
            if not t.startswith(("freq:", "subject:", "subcat:", "era:"))
        ]
        kept.append(f"freq:{tier}")
        kept.append(f"subject:{sanitize_tag_value(subject)}")
        kept.append(f"subcat:{sanitize_tag_value(subcat_label)}")
        if secondary_subject:
            kept.append(f"subcat2:{sanitize_tag_value(secondary_subject)}")
        if year > 0:
            kept.append(get_era_tag(year))
        new_tags = " " + " ".join(kept) + " " if kept else ""

        updates.append((new_flds, new_tags, note_id))

    cursor.executemany("UPDATE notes SET flds = ?, tags = ? WHERE id = ?", updates)
    conn.commit()
    logger.info(f"Applied scores + tags to {len(updates)} notes")
    return len(updates)


def print_report(
    meta: dict[int, NoteMeta],
    scored: dict[int, tuple[int, Tier]],
    subject_score: dict[str, float],
    secondary_subject_score: dict[str, float],
) -> None:
    """Print a frequency analysis summary."""
    tier_counts: dict[str, int] = defaultdict(int)
    for _score, tier in scored.values():
        tier_counts[tier] += 1
    total = len(scored)

    print("\n=== Jeopardy Frequency Analysis (blended) ===\n")
    print(f"Total cards scored: {total:,}")
    print("\nTier distribution:")
    for tier in ("high", "medium", "low", "rare"):
        cnt = tier_counts[tier]
        pct = (100.0 * cnt / total) if total else 0.0
        print(f"  freq:{tier:<6} {cnt:>7,} ({pct:5.1f}%)")

    secondary_count = sum(1 for m in meta.values() if m[4])
    print(
        f"\nCards with secondary_subject (wordplay+domain): {secondary_count:,} ({100.0*secondary_count/total:.1f}%)"
    )

    print("\nTop subjects by recency-weighted frequency:")
    top = sorted(subject_score.items(), key=lambda kv: kv[1], reverse=True)
    for subject, sc in top[:15]:
        print(f"  {subject:<24} {sc:>10.1f}")

    if secondary_subject_score:
        print("\nTop secondary_subjects (wordplay domain boost):")
        sec_top = sorted(
            secondary_subject_score.items(), key=lambda kv: kv[1], reverse=True
        )
        for subject, sc in sec_top[:10]:
            print(f"  {subject:<24} {sc:>10.1f}")
    print()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Blended frequency scoring + tagging for the Jeopardy deck"
    )
    parser.add_argument("source", help="Source .colpkg (1984–2025, post-merge)")
    parser.add_argument("output", help="Output .colpkg")
    parser.add_argument(
        "--taxonomy",
        default="category_taxonomy.json",
        help="Category taxonomy JSON (default: category_taxonomy.json)",
    )
    parser.add_argument(
        "--analysis-only",
        action="store_true",
        help="Print analysis and exit without writing the deck",
    )
    args = parser.parse_args()

    source_path = Path(args.source)
    output_path = Path(args.output)
    taxonomy_path = Path(args.taxonomy)
    if not source_path.exists():
        logger.error(f"Source not found: {source_path}")
        sys.exit(1)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            logger.info(f"Extracting {source_path}")
            db_path = extract_colpkg(source_path, tmp_path)
            conn = connect_anki(db_path)

            taxonomy = load_taxonomy(taxonomy_path)
            logger.info(f"Loaded taxonomy with {len(taxonomy)} categories")

            meta = read_note_meta(conn, taxonomy)
            answer_score, subcat_score, subject_score, secondary_subject_score = (
                build_frequency_tables(meta)
            )
            logger.info(
                f"Tables: {len(answer_score)} answers, {len(subcat_score)} "
                f"sub-categories, {len(subject_score)} subjects, "
                f"{len(secondary_subject_score)} secondary subjects"
            )
            scored = score_notes(
                meta, answer_score, subcat_score, subject_score, secondary_subject_score
            )
            print_report(meta, scored, subject_score, secondary_subject_score)

            if args.analysis_only:
                logger.info("Analysis-only mode; exiting")
                conn.close()
                return

            if get_jeopardy_field_count(conn) < TOTAL_FIELDS:
                logger.error("Unexpected field count; aborting")
                conn.close()
                sys.exit(1)

            appended = add_frequency_field_and_template(conn)
            apply_scores_and_tags(conn, meta, scored, appended)
            conn.commit()
            conn.close()

            extract_dir = tmp_path / "extract"
            logger.info(f"Repacking to {output_path}")
            repack_colpkg(db_path, extract_dir, output_path)
            logger.info(f"✓ Success! Scored deck written to {output_path}")
    except Exception as exc:
        logger.exception(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
