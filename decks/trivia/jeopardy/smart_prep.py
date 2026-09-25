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
  python -m decks.trivia.jeopardy.smart_prep SOURCE.colpkg OUTPUT.apkg
      [--taxonomy category_taxonomy.json] [--analysis-only]
      [--evidence-report moves.tsv] [--card-report cards.tsv]
      [--deck-name "Jeopardy Smart Prep"]
"""

import argparse
import bisect
import csv
import json
import logging
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from decks.trivia.jeopardy.consolidate_taxonomy import MANUAL_OVERRIDES, NO_CARD_SUBJECTS
from decks.trivia.jeopardy.jeopardy_card_helpers import label_cards
from decks.trivia.jeopardy.jeopardy_consts import (
    BADGE_STYLE_BLOCK,
    BADGE_STYLE_MARKER,
    CARD_AGE_DEFAULT,
    CARD_AGE_WEIGHTS,
    CARD_REPORT_COLUMNS,
    ERA_MODERN_START,
    ERA_RECENT_START,
    EVIDENCE_REPORT_COLUMNS,
    EVIDENCE_REPORT_TOP_N,
    FIELD_AIR_DATE,
    LIVENESS_DEFAULT,
    LIVENESS_WEIGHTS,
    FIELD_ANSWER,
    FIELD_CATEGORY,
    FIELD_DAILY_DOUBLE,
    FIELD_QUESTION,
    FIELD_ROUND,
    FIELD_VALUE,
    FREQ_DETAILS_FIELD_CONFIG_HEX,
    FREQ_DETAILS_FIELD_NAME,
    FREQ_FIELD_CONFIG_HEX,
    FREQ_FIELD_NAME,
    JEOPARDY_NOTETYPE_ID,
    RECENCY_WEIGHTS,
    ROUND_FINAL_JEOPARDY,
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
    SUBCATEGORY_UNCLASSIFIED,
    SUBJECT_OTHER,
    SUBJECT_BADGE_CLASS,
    TAXONOMY_PATH_DEFAULT,
    THEME_WINDOW_YEARS,
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
from decks.trivia.jeopardy.jeopardy_db_helpers import (
    connect_anki,
    merge_plural_variants,
    normalize_answer,
    extract_colpkg,
    get_deck_id,
    pack_apkg,
    protobuf_prepend_to_field1,
    protobuf_get_field,
    protobuf_replace_fields,
    rename_deck,
    require_anki_closed,
    reset_review_progress,
    strip_foreign_decks,
)
from decks.trivia.jeopardy.jeopardy_taxonomy_helpers import (
    build_answer_votes,
    build_clue_vocabulary,
    group_cards_by_category,
    reclassify_by_evidence,
)
from decks.trivia.jeopardy.jeopardy_types import CardSubject, CardText, EvidenceReclassification

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
# Sub-category labels that mean "no topic"; score_notes() gives them no credit.
_NO_TOPIC_SUBCATS: frozenset[str] = frozenset(
    {_DEFAULT_SUBCAT, SUBCATEGORY_UNCLASSIFIED}
)
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

    if rnd == ROUND_FINAL_JEOPARDY:
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


def read_cards(conn: sqlite3.Connection) -> list[CardText]:
    """Read every Jeopardy note's category, clue and normalized answer.

    Args:
        conn: SQLite connection

    Returns:
        One CardText per note (the answer key is "" when it is empty or
        punctuation-only)
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id, flds FROM notes WHERE mid = ?", (JEOPARDY_NOTETYPE_ID,))
    cards: list[CardText] = []
    for note_id, flds in cursor:
        parts = flds.split("\x1f")
        if len(parts) < TOTAL_FIELDS:
            continue
        cards.append(
            CardText(
                note_id=note_id,
                category=parts[FIELD_CATEGORY].strip().upper(),
                clue=parts[FIELD_QUESTION],
                answer_key=normalize_answer(parts[FIELD_ANSWER].strip()),
            )
        )
    return cards


def log_evidence_reclassifications(moved: list[EvidenceReclassification]) -> None:
    """Summarize the evidence moves by source and target, then list the largest."""
    cards = sum(item["notes"] for item in moved)
    logger.info(f"Evidence reclassification: {len(moved)} categories ({cards:,} cards)")
    by_move = Counter((item["source_subject"], item["subject"]) for item in moved)
    for (source, subject), count in by_move.most_common(EVIDENCE_REPORT_TOP_N):
        logger.info(f"  {count:>4} categories  {source} -> {subject}")
    if len(by_move) > EVIDENCE_REPORT_TOP_N:
        logger.info(f"  ... and {len(by_move) - EVIDENCE_REPORT_TOP_N} more pairs")
    for item in moved[:EVIDENCE_REPORT_TOP_N]:
        logger.info(
            f"  {item['category']} ({item['notes']} cards): {item['source_subject']}"
            f" -> {item['subject']}, share {item['mean_share']:.2f}"
            f" vs {item['runner_up']} {item['runner_up_share']:.2f}"
        )
    if len(moved) > EVIDENCE_REPORT_TOP_N:
        logger.info(f"  ... and {len(moved) - EVIDENCE_REPORT_TOP_N} more")


def write_evidence_report(path: Path, moved: list[EvidenceReclassification]) -> None:
    """Write every evidence move as a TSV, for review before a live refresh."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(EVIDENCE_REPORT_COLUMNS)
        for item in moved:
            writer.writerow(
                (
                    item["category"],
                    item["notes"],
                    item["source_subject"],
                    item["subject"],
                    f"{item['mean_share']:.3f}",
                    item["runner_up"],
                    f"{item['runner_up_share']:.3f}",
                )
            )
    logger.info(f"Wrote {len(moved)} evidence reclassifications to {path}")


def refine_taxonomy_with_evidence(
    cards: list[CardText],
    taxonomy: dict[str, tuple[str, str, str]],
    evidence_report: Path | None = None,
) -> dict[str, tuple[str, str, str]]:
    """Move Other and Wordplay categories to the subject their cards point to.

    The name-only LLM pass cannot place a pun like "A NOVEL PASSAGE" or a letter
    game like 'CAPITAL "C"'; their cards can (see jeopardy_taxonomy_helpers).
    Manual overrides are never moved.

    Args:
        cards: every Jeopardy card in the deck
        taxonomy: category -> (subject, sub_category, secondary_subject)
        evidence_report: if set, every move is also written here as a TSV

    Returns:
        A refined copy of the taxonomy (the input is not mutated)
    """
    answers, clues = group_cards_by_category(cards)
    refined, moved = reclassify_by_evidence(
        taxonomy, answers, clues, MANUAL_OVERRIDES.keys()
    )
    log_evidence_reclassifications(moved)
    if evidence_report is not None:
        write_evidence_report(evidence_report, moved)
    return refined


def log_card_subjects(labeled: list[CardSubject]) -> None:
    """Summarize the per-card subjects by category and by subject."""
    categories = len({item["category"] for item in labeled})
    logger.info(f"Per-card subjects: {len(labeled):,} cards in {categories} categories")
    by_move = Counter((item["source_subject"], item["subject"]) for item in labeled)
    for (source, subject), count in by_move.most_common(EVIDENCE_REPORT_TOP_N):
        logger.info(f"  {count:>6,} cards  {source} -> {subject}")
    if len(by_move) > EVIDENCE_REPORT_TOP_N:
        logger.info(f"  ... and {len(by_move) - EVIDENCE_REPORT_TOP_N} more pairs")


def write_card_report(path: Path, labeled: list[CardSubject]) -> None:
    """Write every per-card subject as a TSV, for review before a live refresh."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(CARD_REPORT_COLUMNS)
        for item in labeled:
            writer.writerow(
                (
                    item["note_id"],
                    item["category"],
                    item["source_subject"],
                    item["subject"],
                    f"{item['gap']:.2f}",
                    item["runner_up"],
                )
            )
    logger.info(f"Wrote {len(labeled)} per-card subjects to {path}")


def label_cards_by_evidence(
    cards: list[CardText],
    taxonomy: dict[str, tuple[str, str, str]],
    card_report: Path | None = None,
) -> dict[int, str]:
    """Give each card of a subject-less category the subject of its own evidence.

    Runs after the category rules, on the refined taxonomy, so the categories they
    moved vote and teach the clue model. Only a card's badge, theme text and
    subject tag change: scoring still sees its category's subject. Categories
    pinned as "Other" in MANUAL_OVERRIDES (the grab-bags) are labeled too;
    NO_CARD_SUBJECTS opts a category out.

    Args:
        cards: every Jeopardy card in the deck
        taxonomy: the taxonomy after `refine_taxonomy_with_evidence`
        card_report: if set, every label is also written here as a TSV

    Returns:
        note id -> subject, for the cards that got one
    """
    answers, clues = group_cards_by_category(cards)
    labeled = label_cards(
        cards,
        taxonomy,
        NO_CARD_SUBJECTS,
        build_answer_votes(answers, taxonomy),
        build_clue_vocabulary(clues, taxonomy),
    )
    log_card_subjects(labeled)
    if card_report is not None:
        write_card_report(card_report, labeled)
    return {item["note_id"]: item["subject"] for item in labeled}


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
    # Per-note component raw values. "Other" subject and the "Miscellaneous" /
    # "Unclassified" sub-categories are the ABSENCE of a topic (grab-bag/unclassified),
    # so they earn no topic-frequency credit — their components are zeroed and the
    # card is scored on its exact-answer frequency alone.
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
            if subcat_label in _NO_TOPIC_SUBCATS
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
    the score plus short tier/subject class codes are persisted. The tier is
    conveyed by the badge colour; the subject is rendered from its class code by
    a CSS ::after rule, which costs ~4 bytes/note instead of the ~36 the literal
    text plus markup would take. SUBJECT_OTHER carries no code, so those badges
    render as the bare score.

    Args:
        score: 0-100 frequency score
        tier: Frequency tier, which selects the colour class
        subject: Broad subject, rendered into the badge via its class code

    Returns:
        Compact HTML for the note's Frequency Score field
    """
    classes = f"fq {TIER_BADGE_CLASS.get(tier, 'r')}"
    subject_code = SUBJECT_BADGE_CLASS.get(subject)
    if subject_code:
        classes = f"{classes} {subject_code}"
    return f'<b class="{classes}">{score}</b>'


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


def add_frequency_fields_and_template(conn: sqlite3.Connection) -> tuple[int, int]:
    """Add score/detail fields and render the detail only on the card back.

    Idempotent: if the field already exists, does nothing and reports that the
    notes already carry the extra field segment.

    Args:
        conn: SQLite connection

    Returns:
        The note-field ordinals for the score and details fields.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT ord, name FROM fields WHERE ntid = ? ORDER BY ord",
        (JEOPARDY_NOTETYPE_ID,),
    )
    rows = cursor.fetchall()
    field_ords = {name: ord_ for ord_, name in rows}
    for name, config_hex in (
        (FREQ_FIELD_NAME, FREQ_FIELD_CONFIG_HEX),
        (FREQ_DETAILS_FIELD_NAME, FREQ_DETAILS_FIELD_CONFIG_HEX),
    ):
        if name in field_ords:
            continue
        new_ord = len(rows)
        cursor.execute(
            "INSERT INTO fields (ntid, ord, name, config) VALUES (?, ?, ?, ?)",
            (JEOPARDY_NOTETYPE_ID, new_ord, name, bytes.fromhex(config_hex)),
        )
        field_ords[name] = new_ord
        rows.append((new_ord, name))
        logger.info(f"Added '{name}' field (ord {new_ord})")

    # Inject the field reference into the front template (protobuf field 1).
    cursor.execute(
        "SELECT ord, config FROM templates WHERE ntid = ?", (JEOPARDY_NOTETYPE_ID,)
    )
    for tmpl_ord, tconfig in cursor.fetchall():
        new_config = tconfig
        if b"Frequency Score" not in tconfig:
            prefix = "{{#Frequency Score}}{{Frequency Score}}{{/Frequency Score}}\n"
            new_config = protobuf_prepend_to_field1(new_config, prefix)
        if b"Frequency Details" not in new_config:
            suffix = "\n{{#Frequency Details}}{{Frequency Details}}{{/Frequency Details}}"
            afmt = protobuf_get_field(new_config, 2)
            if afmt is not None:
                new_config = protobuf_replace_fields(
                    new_config, {2: (afmt.decode("utf-8") + suffix).encode("utf-8")}
                )
        if new_config == tconfig:
            continue
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
    logger.info("Frequency score/detail fields and template references are ready")
    return field_ords[FREQ_FIELD_NAME], field_ords[FREQ_DETAILS_FIELD_NAME]


def build_frequency_details(
    subject: str,
    score: int,
    recent_subject_counts: dict[str, int],
    start_year: int,
    latest_year: int,
) -> str:
    """Render the recent subject count shown on the back of each card."""
    appeared = recent_subject_counts.get(subject, 0)
    return (
        '<div class="fq-details">'
        f"<b>Frequently asked theme:</b> {subject}<br>"
        f"<b>Appeared:</b> {appeared:,} times from {start_year}-{latest_year}<br>"
        f"<b>Study priority:</b> {score}/100"
        "</div>"
    )


def build_recent_subject_counts(
    meta: dict[int, NoteMeta], latest_year: int, card_subjects: Mapping[int, str]
) -> tuple[dict[str, int], int]:
    """Count subject cards once for the recent display window.

    A card counts under the subject it is shown with: its own if it has one
    (see `label_cards_by_evidence`), else its category's.
    """
    start_year = latest_year - THEME_WINDOW_YEARS + 1
    counts: dict[str, int] = defaultdict(int)
    for note_id, m in meta.items():
        _answer, _subcat, subject, _label, _secondary, year, _stake = m
        if start_year <= year <= latest_year:
            counts[card_subjects.get(note_id, subject)] += 1
    return dict(counts), start_year


def apply_scores_and_tags(
    conn: sqlite3.Connection,
    meta: dict[int, NoteMeta],
    scored: dict[int, tuple[int, Tier]],
    score_ord: int,
    details_ord: int,
    latest_year: int,
    recent_subject_counts: dict[str, int],
    start_year: int,
    card_subjects: Mapping[int, str],
) -> int:
    """Write the badge field + freq/subject/subcat/era tags onto every note.

    Args:
        conn: SQLite connection
        meta: note_id -> NoteMeta
        scored: note_id -> (score, tier)
        score_ord: Note-field ordinal for the score badge
        details_ord: Note-field ordinal for the back-of-card details
        card_subjects: note id -> the subject a card is shown with instead of its
            category's (badge, theme text and subject tag; the sub-category tag
            stays its category's)

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
        _ak, _ck, category_subject, subcat_label, secondary_subject, year, _stake = (
            meta[note_id]
        )
        subject = card_subjects.get(note_id, category_subject)
        badge = badge_html(score, tier, subject)
        details = build_frequency_details(
            subject, score, recent_subject_counts, start_year, latest_year
        )

        parts = flds.split("\x1f")
        while len(parts) <= details_ord:
            parts.append("")
        parts[score_ord] = badge
        parts[details_ord] = details
        new_flds = "\x1f".join(parts)

        # Rebuild tags: drop any prior smart-prep tags, then add fresh ones.
        kept = [
            t
            for t in tags.split()
            if not t.startswith(("freq:", "subject:", "subcat:", "subcat2:", "era:"))
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
    conn: sqlite3.Connection,
    taxonomy_path: Path,
    evidence_report: Path | None = None,
    card_report: Path | None = None,
) -> tuple[
    dict[int, NoteMeta],
    dict[int, tuple[int, Tier]],
    dict[str, float],
    dict[str, float],
    dict[int, str],
]:
    """Read notes from `conn` and score every one of them.

    Args:
        conn: SQLite connection to a collection holding the Jeopardy notes
        taxonomy_path: Path to category_taxonomy.json
        evidence_report: if set, the evidence reclassifications are written here
        card_report: if set, the per-card subjects are written here

    Returns:
        (meta, scored, subject_score, secondary_subject_score, card_subjects).
        Scoring uses each category's subject; `card_subjects` (note id -> the
        subject a card is shown with) only changes what is displayed.
    """
    taxonomy = load_taxonomy(taxonomy_path)
    logger.info(f"Loaded taxonomy with {len(taxonomy)} categories")
    cards = read_cards(conn)
    taxonomy = refine_taxonomy_with_evidence(cards, taxonomy, evidence_report)
    card_subjects = label_cards_by_evidence(cards, taxonomy, card_report)

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
    return meta, scored, subject_score, secondary_subject_score, card_subjects


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
        default=TAXONOMY_PATH_DEFAULT,
        help="Category taxonomy JSON (default: data/category_taxonomy.json beside this script)",
    )
    parser.add_argument(
        "--deck-name",
        default="Jeopardy Smart Prep",
        help="Name for the output deck (default: 'Jeopardy Smart Prep')",
    )
    parser.add_argument(
        "--evidence-report",
        metavar="PATH",
        default=None,
        help=(
            "Write every evidence reclassification (category, cards, from, to, "
            "shares) to PATH as a TSV; combine with --analysis-only to preview"
        ),
    )
    parser.add_argument(
        "--card-report",
        metavar="PATH",
        default=None,
        help=(
            "Write every per-card subject (note id, category, from, to, gap) to "
            "PATH as a TSV; combine with --analysis-only to preview"
        ),
    )
    parser.add_argument(
        "--analysis-only",
        action="store_true",
        help="Print analysis and exit without writing the deck",
    )
    parser.add_argument(
        "--clean-export",
        metavar="PATH",
        help=(
            "Write a full shareable copy of the current collection to PATH with all "
            "review progress cleared. Keeps the full deck content but removes due "
            "dates, intervals, and revlog data."
        ),
    )
    parser.add_argument(
        "--clean-deck-name",
        default="jeopardy_enhanced_clean",
        help="Name to assign to the exported clean deck (default: 'jeopardy_enhanced_clean')",
    )
    args = parser.parse_args()

    if args.clean_export:
        if not args.source and not args.live_db:
            parser.error("source is required when using --clean-export unless --live-db is set")
    elif not args.analysis_only and not args.output and not args.live_db:
        parser.error("output is required unless --live-db or --analysis-only is set")
    if not args.clean_export and not args.source and not args.live_db:
        parser.error("source is required unless --live-db is set")

    taxonomy_path = Path(args.taxonomy)
    evidence_report = Path(args.evidence_report) if args.evidence_report else None
    card_report = Path(args.card_report) if args.card_report else None

    # Refresh mode: read AND write the live collection, so manual note edits are
    # what gets scored. No .colpkg is involved, which also removes any chance of
    # the source drifting out of sync with the collection.
    if args.clean_export:
        if args.live_db:
            live_db_path = Path(args.live_db).expanduser().resolve()
            if not live_db_path.exists():
                logger.error(f"Live DB not found: {live_db_path}")
                sys.exit(1)
            require_anki_closed(live_db_path)
            live_conn = connect_anki(live_db_path)
            clean_deck_id = get_deck_id(live_conn)
            rename_deck(live_conn, clean_deck_id, args.clean_deck_name)
            reset_review_progress(live_conn)
            live_conn.commit()
            live_conn.close()
            pack_apkg(live_db_path, Path(args.clean_export))
            logger.info(f"✓ Clean shareable export written to {args.clean_export}")
            return

        source_path = Path(args.source)
        if not source_path.exists():
            logger.error(f"Source not found: {source_path}")
            sys.exit(1)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                db_path = extract_colpkg(source_path, tmp_path)
                conn = connect_anki(db_path)
                clean_deck_id = get_deck_id(conn)
                rename_deck(conn, clean_deck_id, args.clean_deck_name)
                reset_review_progress(conn)
                conn.commit()
                conn.close()
                pack_apkg(db_path, Path(args.clean_export))
                logger.info(f"✓ Clean shareable export written to {args.clean_export}")
        except Exception as exc:
            logger.exception(f"Error: {exc}")
            sys.exit(1)
        return

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
        meta, scored, subject_score, secondary_subject_score, card_subjects = (
            compute_scores(live_conn, taxonomy_path, evidence_report, card_report)
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
        score_ord, details_ord = add_frequency_fields_and_template(live_conn)
        latest_year = max((item[5] for item in meta.values()), default=0)
        recent_subject_counts, start_year = build_recent_subject_counts(
            meta, latest_year, card_subjects
        )
        apply_scores_and_tags(
            live_conn,
            meta,
            scored,
            score_ord,
            details_ord,
            latest_year,
            recent_subject_counts,
            start_year,
            card_subjects,
        )
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
            meta, scored, subject_score, secondary_subject_score, card_subjects = (
                compute_scores(conn, taxonomy_path, evidence_report, card_report)
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
            score_ord, details_ord = add_frequency_fields_and_template(conn)
            latest_year = max((item[5] for item in meta.values()), default=0)
            recent_subject_counts, start_year = build_recent_subject_counts(
                meta, latest_year, card_subjects
            )
            apply_scores_and_tags(
                conn,
                meta,
                scored,
                score_ord,
                details_ord,
                latest_year,
                recent_subject_counts,
                start_year,
                card_subjects,
            )

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
