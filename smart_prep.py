#!/usr/bin/env python3
"""Jeopardy Smart Prep — blended frequency scoring, tagging, and on-card display.

For a collection that already spans 1984–2025 (see update_collection.py) and a
category taxonomy (see classify_categories.py), this:

  1. Computes a recency-weighted, stake-weighted frequency for each card's exact
     ANSWER, its SUB-CATEGORY, and its broad SUBJECT.
  2. Blends those three (as percentiles) into a single 0-100 frequency score and
     a tier (freq:high/medium/low/rare).
  3. Adds a "Frequency Score" field to the Jeopardy note type + renders it on the
     card as a colored badge, and tags each note (freq:/subject:/subcat:/era:).
  4. Strips all non-Jeopardy decks and outputs a single-deck .apkg that merges
     cleanly into an existing Anki collection without replacing it.

Usage:
  python smart_prep.py SOURCE.colpkg OUTPUT.apkg
      [--taxonomy category_taxonomy.json] [--analysis-only]
      [--deck-name "Jeopardy Smart Prep"]
"""

import argparse
import bisect
import json
import logging
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from jeopardy_consts import (
    BADGE_STYLE_BLOCK,
    BADGE_STYLE_MARKER,
    CARD_AGE_DEFAULT,
    CARD_AGE_WEIGHTS,
    ERA_MODERN_START,
    ERA_RECENT_START,
    FIELD_AIR_DATE,
    LIVENESS_DEFAULT,
    LIVENESS_WEIGHTS,
    FIELD_ANSWER,
    FIELD_CATEGORY,
    FIELD_DAILY_DOUBLE,
    FIELD_ROUND,
    FIELD_VALUE,
    FREQ_FIELD_CONFIG_HEX,
    FREQ_FIELD_NAME,
    JEOPARDY_NOTETYPE_ID,
    RECENCY_WEIGHTS,
    STAKE_DD_DJ,
    STAKE_DD_J,
    STAKE_DJ_MAX,
    STAKE_DJ_MIN,
    STAKE_DJ_VALUE_MAX,
    STAKE_DJ_VALUE_MIN,
    STAKE_FINAL_JEOPARDY,
    STAKE_J_MAX,
    STAKE_J_MIN,
    STAKE_J_VALUE_MAX,
    STAKE_J_VALUE_MIN,
    SUBJECT_OTHER,
    TIER_BADGE_CLASS,
    TIER_HIGH_MIN,
    TIER_LOW_MIN,
    TIER_MEDIUM_MIN,
    TOTAL_FIELDS,
    USN_PENDING,
    WEIGHT_ANSWER,
    WEIGHT_SUBCATEGORY,
    WEIGHT_SUBJECT,
)
from jeopardy_db_helpers import (
    connect_anki,
    merge_plural_variants,
    normalize_answer,
    extract_colpkg,
    pack_apkg,
    protobuf_prepend_to_field1,
    rename_deck,
    require_anki_closed,
    strip_foreign_decks,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger(__name__)

Tier = Literal["high", "medium", "low", "rare"]

# Per-note computed metadata used for scoring then writing.
# (answer_key, subcat_key, subject, subcat_label, secondary_subject, year, stake_mult)
NoteMeta = tuple[str, str, str, str, str, int, float]

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


def liveness_weight(last_seen_year: int) -> float:
    """How alive a topic still is, from the last year its answer appeared.

    Answers whose most recent appearance is decades old are effectively retired,
    however often they came up at the time. This is the primary "no longer
    relevant" discriminator.
    """
    return LIVENESS_WEIGHTS.get(last_seen_year, LIVENESS_DEFAULT)


def card_age_weight(year: int) -> float:
    """Mild decay on a card's own air year (phrasing drifts over time)."""
    return CARD_AGE_WEIGHTS.get(year, CARD_AGE_DEFAULT)


def build_answer_last_seen(meta: dict[int, NoteMeta]) -> dict[str, int]:
    """Map each answer TOPIC to the most recent year it appeared in the corpus.

    Keys are normalized and plural-folded, so "talons" and "talon" count as one
    topic. Matching on the raw text instead would make a live topic look retired
    purely because its most recent airing used a different spelling.

    Args:
        meta: note_id -> NoteMeta

    Returns:
        normalized answer key -> latest air year
    """
    years_by_key: dict[str, list[int]] = defaultdict(list)
    for answer_key, _ck, _s, _lbl, _sec, year, _stake in meta.values():
        if answer_key:
            years_by_key[answer_key].append(year)
    return {k: max(v) for k, v in merge_plural_variants(dict(years_by_key)).items()}


def topic_liveness_for(answer_key: str, answer_last_seen: dict[str, int]) -> float:
    """Liveness for an answer, checking its plural-folded form as a fallback."""
    if not answer_key:
        return LIVENESS_DEFAULT
    last = answer_last_seen.get(answer_key)
    if last is None and answer_key.endswith("s"):
        last = answer_last_seen.get(answer_key[:-1])
    return liveness_weight(last) if last is not None else LIVENESS_DEFAULT


def get_era_tag(year: int) -> str:
    """Era tag for a year: era:recent / era:modern / era:old."""
    if year >= ERA_RECENT_START:
        return "era:recent"
    if year >= ERA_MODERN_START:
        return "era:modern"
    return "era:old"


def compute_stake_multiplier(
    round_name: str, value_str: str, daily_double_str: str
) -> float:
    """Stake multiplier based on round, dollar value, and daily-double status.

    Priority (highest → lowest): Final Jeopardy > DD (Double Jeopardy) > DD (Jeopardy)
    > Double Jeopardy by value (1.1–1.5) > Jeopardy by value (0.6–1.0).
    """
    rnd = round_name.strip()
    is_dd = daily_double_str.strip().lower() not in ("", "0", "false", "no")

    if rnd == "Final Jeopardy":
        return STAKE_FINAL_JEOPARDY
    if is_dd:
        return STAKE_DD_DJ if rnd == "Double Jeopardy" else STAKE_DD_J

    try:
        value = int(value_str.strip().lstrip("$").replace(",", ""))
    except (ValueError, AttributeError):
        value = 0

    if rnd == "Double Jeopardy":
        if value <= 0:
            return (STAKE_DJ_MIN + STAKE_DJ_MAX) / 2
        frac = min(
            max(value - STAKE_DJ_VALUE_MIN, 0), STAKE_DJ_VALUE_MAX - STAKE_DJ_VALUE_MIN
        ) / (STAKE_DJ_VALUE_MAX - STAKE_DJ_VALUE_MIN)
        return STAKE_DJ_MIN + frac * (STAKE_DJ_MAX - STAKE_DJ_MIN)

    # Jeopardy (or unknown round)
    if value <= 0:
        return (STAKE_J_MIN + STAKE_J_MAX) / 2
    frac = min(
        max(value - STAKE_J_VALUE_MIN, 0), STAKE_J_VALUE_MAX - STAKE_J_VALUE_MIN
    ) / (STAKE_J_VALUE_MAX - STAKE_J_VALUE_MIN)
    return STAKE_J_MIN + frac * (STAKE_J_MAX - STAKE_J_MIN)


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
        answer_key = normalize_answer(answer)
        subcat_key = subcat_label.casefold()
        stake_mult = compute_stake_multiplier(
            parts[FIELD_ROUND], parts[FIELD_VALUE], parts[FIELD_DAILY_DOUBLE]
        )
        meta[note_id] = (
            answer_key,
            subcat_key,
            subject,
            subcat_label,
            secondary_subject,
            year,
            stake_mult,
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
        stake_mult,
    ) in meta.values():
        weight = recency_weight(year) * stake_mult
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
    answer_last_seen: dict[str, int],
) -> dict[int, tuple[int, Tier]]:
    """Compute the blended 0-100 score and tier for every note.

    Three stages:

    1. TOPIC BLEND — percentile of the note's answer, sub-category, and subject
       frequencies, combined with the configured weights. "How much does this
       material come up at all?"
    2. RELEVANCE DECAY — the blend is multiplied by `liveness_weight` (has this
       answer appeared recently, or did it retire in 1994?) and `card_age_weight`
       (a gentler nudge on the card's own air year). A frequently-asked topic
       that stopped appearing decades ago is demoted; an old card about a topic
       still in rotation keeps most of its value.
    3. RE-PERCENTILE — the decayed values are ranked again so the final 0-100 is
       a true percentile. A score of 85 means "more study-worthy than 85% of the
       deck", which keeps the tier thresholds meaningful.

    For the subject component, uses max(primary_subject, secondary_subject) so that
    a wordplay category embedding a knowledge domain (e.g. "SCIENCE BEFORE & AFTER")
    gets credit for whichever domain scores higher.

    Args:
        meta: note_id -> NoteMeta
        answer_score: answer_key -> recency-weighted frequency
        subcat_score: subcat_key -> recency-weighted frequency
        subject_score: subject -> recency-weighted frequency
        secondary_subject_score: secondary_subject -> recency-weighted frequency
        answer_last_seen: answer_key -> most recent year that answer appeared

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
        (
            answer_key,
            subcat_key,
            subject,
            subcat_label,
            secondary_subject,
            _year,
            _stake,
        ) = m
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

    # Stages 1-2: topic blend, then decay by topic liveness and card age.
    decayed: dict[int, float] = {}
    for nid, m in meta.items():
        answer_key, _ck, _subj, _lbl, _sec, year, _stake = m
        blended = (
            WEIGHT_ANSWER * pct_a(av[nid])
            + WEIGHT_SUBCATEGORY * pct_c(cv[nid])
            + WEIGHT_SUBJECT * pct_s(sv[nid])
        )
        liveness = topic_liveness_for(answer_key, answer_last_seen)
        decayed[nid] = blended * liveness * card_age_weight(year)

    # Stage 3: re-rank so the published score is a true percentile.
    pct_final = make_percentile_fn(list(decayed.values()))
    scored: dict[int, tuple[int, Tier]] = {}
    for nid in meta:
        final = 100.0 * pct_final(decayed[nid])
        scored[nid] = (int(round(final)), tier_from_score(final))
    return scored


def badge_html(score: int, tier: Tier, subject: str) -> str:
    """Render the on-card frequency badge.

    Kept deliberately tiny: this string is stored on every one of ~452K notes,
    so the styling lives in the card template (see BADGE_STYLE_BLOCK) and only
    the score plus a one-character tier class is persisted. The tier is conveyed
    by the badge colour, and the subject is already available as a `subject:`
    tag, so neither is duplicated here.

    Args:
        score: 0-100 frequency score
        tier: Frequency tier, which selects the colour class
        subject: Retained for signature compatibility; not stored on the note

    Returns:
        Compact HTML for the note's Frequency Score field
    """
    del subject  # available via the subject: tag; not worth ~9MB to duplicate
    return f'<b class="fq {TIER_BADGE_CLASS.get(tier, "r")}">{score}</b>'


def get_jeopardy_field_count(conn: sqlite3.Connection) -> int:
    """Return the current number of fields on the Jeopardy note type."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM fields WHERE ntid = ?", (JEOPARDY_NOTETYPE_ID,)
    )
    return int(cursor.fetchone()[0])


def ensure_badge_styles(conn: sqlite3.Connection) -> bool:
    """Put the badge stylesheet in the card template, once.

    The per-note badge carries only a tier class, so the rules that colour it
    have to live somewhere shared. Injecting them into the template keeps ~150
    bytes off every note. Idempotent via BADGE_STYLE_MARKER.

    Args:
        conn: SQLite connection

    Returns:
        True if the stylesheet was injected, False if it was already present
    """
    cursor = conn.cursor()
    injected = False
    for tmpl_ord, tconfig in cursor.execute(
        "SELECT ord, config FROM templates WHERE ntid = ?", (JEOPARDY_NOTETYPE_ID,)
    ).fetchall():
        if BADGE_STYLE_MARKER.encode() in tconfig:
            continue
        cursor.execute(
            "UPDATE templates SET config = ?, mtime_secs = ?, usn = ? "
            "WHERE ntid = ? AND ord = ?",
            (
                protobuf_prepend_to_field1(tconfig, BADGE_STYLE_BLOCK),
                int(time.time()),
                USN_PENDING,
                JEOPARDY_NOTETYPE_ID,
                tmpl_ord,
            ),
        )
        injected = True
    if injected:
        logger.info("Injected badge stylesheet into card template")
    return injected


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
        _ak, _ck, subject, subcat_label, secondary_subject, year, _stake = meta[note_id]
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


def compute_scores(
    conn: sqlite3.Connection, taxonomy_path: Path
) -> tuple[
    dict[int, NoteMeta], dict[int, tuple[int, Tier]], dict[str, float], dict[str, float]
]:
    """Read notes from `conn` and score every one of them.

    Args:
        conn: SQLite connection to a collection holding the Jeopardy notes
        taxonomy_path: Path to category_taxonomy.json

    Returns:
        (meta, scored, subject_score, secondary_subject_score)
    """
    taxonomy = load_taxonomy(taxonomy_path)
    logger.info(f"Loaded taxonomy with {len(taxonomy)} categories")

    meta = read_note_meta(conn, taxonomy)
    answer_score, subcat_score, subject_score, secondary_subject_score = (
        build_frequency_tables(meta)
    )
    answer_last_seen = build_answer_last_seen(meta)
    logger.info(
        f"Tables: {len(answer_score)} answers, {len(subcat_score)} sub-categories, "
        f"{len(subject_score)} subjects, {len(secondary_subject_score)} secondary subjects"
    )
    scored = score_notes(
        meta,
        answer_score,
        subcat_score,
        subject_score,
        secondary_subject_score,
        answer_last_seen,
    )
    return meta, scored, subject_score, secondary_subject_score


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Blended frequency scoring + tagging for the Jeopardy deck"
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Source .colpkg. Omit when --live-db is set (the live collection is read directly).",
    )
    parser.add_argument(
        "output",
        nargs="?",
        help="Output .apkg (required unless --live-db is set)",
    )
    parser.add_argument(
        "--live-db",
        metavar="PATH",
        default=None,
        help=(
            "Write scores + tags directly to a live collection.anki2 instead of "
            "producing an .apkg. Preserves all manual edits; Anki must be closed."
        ),
    )
    parser.add_argument(
        "--taxonomy",
        default="category_taxonomy.json",
        help="Category taxonomy JSON (default: category_taxonomy.json)",
    )
    parser.add_argument(
        "--deck-name",
        default="Jeopardy Smart Prep",
        help="Name for the output deck (default: 'Jeopardy Smart Prep')",
    )
    parser.add_argument(
        "--analysis-only",
        action="store_true",
        help="Print analysis and exit without writing the deck",
    )
    args = parser.parse_args()

    if not args.analysis_only and not args.output and not args.live_db:
        parser.error("output is required unless --live-db or --analysis-only is set")
    if not args.source and not args.live_db:
        parser.error("source is required unless --live-db is set")

    taxonomy_path = Path(args.taxonomy)

    # Refresh mode: read AND write the live collection, so manual note edits are
    # what gets scored. No .colpkg is involved, which also removes any chance of
    # the source drifting out of sync with the collection.
    if args.live_db:
        live_db_path = Path(args.live_db).expanduser().resolve()
        if not live_db_path.exists():
            logger.error(f"Live DB not found: {live_db_path}")
            sys.exit(1)
        require_anki_closed(live_db_path)

        if not args.analysis_only:
            backup_path = live_db_path.with_name(live_db_path.name + ".bak")
            shutil.copy2(live_db_path, backup_path)
            logger.info(f"Backed up live DB to {backup_path}")

        live_conn = connect_anki(live_db_path)
        meta, scored, subject_score, secondary_subject_score = compute_scores(
            live_conn, taxonomy_path
        )
        print_report(meta, scored, subject_score, secondary_subject_score)

        if args.analysis_only:
            logger.info("Analysis-only mode; exiting")
            live_conn.close()
            return

        if get_jeopardy_field_count(live_conn) < TOTAL_FIELDS:
            logger.error("Unexpected field count in live DB; aborting")
            live_conn.close()
            sys.exit(1)
        ensure_badge_styles(live_conn)
        appended = add_frequency_field_and_template(live_conn)
        apply_scores_and_tags(live_conn, meta, scored, appended)
        live_conn.commit()
        live_conn.close()
        logger.info("✓ Scores + tags written to live collection (no import needed)")
        return

    source_path = Path(args.source)
    if not source_path.exists():
        logger.error(f"Source not found: {source_path}")
        sys.exit(1)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            logger.info(f"Extracting {source_path}")
            db_path = extract_colpkg(source_path, tmp_path)
            conn = connect_anki(db_path)
            meta, scored, subject_score, secondary_subject_score = compute_scores(
                conn, taxonomy_path
            )
            print_report(meta, scored, subject_score, secondary_subject_score)
            conn.close()

            if args.analysis_only:
                logger.info("Analysis-only mode; exiting")
                return

            # Initial setup mode: build a fresh .apkg for first-time import.
            output_path = Path(args.output)
            conn = connect_anki(db_path)
            if get_jeopardy_field_count(conn) < TOTAL_FIELDS:
                logger.error("Unexpected field count; aborting")
                conn.close()
                sys.exit(1)

            ensure_badge_styles(conn)
            appended = add_frequency_field_and_template(conn)
            apply_scores_and_tags(conn, meta, scored, appended)

            # Find the Jeopardy deck, rename it, then strip all other decks so
            # the output .apkg contains only the new Jeopardy Smart Prep deck.
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM decks WHERE name LIKE '%Jeopardy%' ORDER BY id LIMIT 1"
            )
            row = cursor.fetchone()
            if not row:
                logger.error("Could not find Jeopardy deck; aborting")
                conn.close()
                sys.exit(1)
            jeopardy_deck_id = int(row[0])

            rename_deck(conn, jeopardy_deck_id, args.deck_name)
            strip_foreign_decks(conn, jeopardy_deck_id)

            conn.commit()
            conn.close()

            logger.info(f"Packing .apkg to {output_path}")
            pack_apkg(db_path, output_path)
            logger.info(f"✓ Success! Deck '{args.deck_name}' written to {output_path}")
    except Exception as exc:
        logger.exception(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
