"""Constants for Jeopardy collection processing."""

from typing import Final

# Anki note field indices (0-based, \x1f-delimited)
FIELD_SHOW_NUMBER: Final[int] = 0
FIELD_AIR_DATE: Final[int] = 1
FIELD_EXTRA_INFO: Final[int] = 2
FIELD_ROUND: Final[int] = 3
FIELD_COORDS: Final[int] = 4
FIELD_CATEGORY: Final[int] = 5
FIELD_ORDER: Final[int] = 6
FIELD_VALUE: Final[int] = 7
FIELD_DAILY_DOUBLE: Final[int] = 8
FIELD_QUESTION: Final[int] = 9
FIELD_LINKS: Final[int] = 10
FIELD_ANSWER: Final[int] = 11
FIELD_CORRECT_ATTEMPTS: Final[int] = 12
FIELD_WRONG_ATTEMPTS: Final[int] = 13

TOTAL_FIELDS: Final[int] = 14

# Anki notetype IDs (from the existing collection)
JEOPARDY_NOTETYPE_ID: Final[int] = 1560061137470

# Deck names. The legacy deck holds the original 1984-2019 import; everything is
# consolidated into the Smart Prep deck, with dead cards parked in its subdeck.
DECK_LEGACY_NAME: Final[str] = "Jeopardy"
DECK_TARGET_NAME: Final[str] = "Jeopardy Smart Prep"
DECK_ARCHIVE_NAME: Final[str] = "Jeopardy Smart Prep::Archive"

# Anki sentinel values.
USN_PENDING: Final[int] = -1  # "modified locally, not yet synced"
GRAVE_TYPE_DECK: Final[int] = 2  # graves.type for a deleted deck

# Blended-score tier thresholds (on the 0-100 blended percentile score).
TIER_HIGH_MIN: Final[float] = 70.0
TIER_MEDIUM_MIN: Final[float] = 40.0
TIER_LOW_MIN: Final[float] = 15.0

# Blend weights for the per-card frequency score (must sum to 1.0).
# Each component is a 0-1 percentile of the card's recency-weighted frequency
# for that dimension; the blend is scaled to 0-100.
WEIGHT_ANSWER: Final[float] = 0.40
WEIGHT_SUBCATEGORY: Final[float] = 0.35
WEIGHT_SUBJECT: Final[float] = 0.25

# New note-type field that displays the score on the card.
FREQ_FIELD_NAME: Final[str] = "Frequency Score"
# Reused verbatim from the existing Jeopardy fields (Arial / size 20 /
# {"media":[]}) — a valid Anki FieldConfig protobuf.
FREQ_FIELD_CONFIG_HEX: Final[str] = "1a05417269616c2014fa0f0c7b226d65646961223a5b5d7d"

# Badge colors by tier (most-frequent = warm/urgent, rare = muted).
TIER_BADGE_COLORS: Final[dict[str, str]] = {
    "high": "#c0392b",
    "medium": "#d68910",
    "low": "#2471a3",
    "rare": "#566573",
}

# One-character CSS class per tier. The badge is stored on all ~452K notes, so
# every byte here costs ~450KB of collection size; the styling itself lives once
# in the card template (BADGE_STYLE_BLOCK) rather than inline on each note.
# Inline styling previously cost 174 bytes/note = 79MB, which pushed the
# collection past AnkiWeb's 300MB upload ceiling.
TIER_BADGE_CLASS: Final[dict[str, str]] = {
    "high": "h",
    "medium": "m",
    "low": "l",
    "rare": "r",
}
BADGE_STYLE_MARKER: Final[str] = "fq-badge-css"
BADGE_STYLE_BLOCK: Final[str] = (
    f"<style>/*{BADGE_STYLE_MARKER}*/"
    ".fq{display:inline-block;margin:4px 0;padding:2px 10px;border-radius:12px;"
    "color:#fff;font-size:12px;font-weight:bold}"
    f".fq.h{{background:{TIER_BADGE_COLORS['high']}}}"
    f".fq.m{{background:{TIER_BADGE_COLORS['medium']}}}"
    f".fq.l{{background:{TIER_BADGE_COLORS['low']}}}"
    f".fq.r{{background:{TIER_BADGE_COLORS['rare']}}}"
    "</style>\n"
)

# Recency weights by year (1984-2026)
# Updated to reflect actual data: 1984-2019 in current collection,
# 2020-2025 in supplemental dataset
RECENCY_WEIGHTS: Final[dict[int, float]] = {
    **{y: 1.0 for y in range(2020, 2027)},  # 2020-2026: peak weight
    2019: 0.8,
    2018: 0.6,
    2017: 0.5,
    2016: 0.4,
    **{y: 0.3 for y in range(2010, 2016)},  # 2010-2015
    **{y: 0.2 for y in range(1984, 2010)},  # pre-2010
}

# Era tag boundaries
ERA_RECENT_START: Final[int] = 2020
ERA_MODERN_START: Final[int] = 2010

# --- Topic liveness -------------------------------------------------------
# Keyed by the most recent year an ANSWER appeared anywhere in the corpus.
# This is the sharpest "still relevant?" signal: an answer that stopped showing
# up in 1994 is dead no matter how often it appeared back then, whereas one last
# seen in 2024 is live regardless of how old the individual card is.
LIVENESS_WEIGHTS: Final[dict[int, float]] = {
    **{y: 1.00 for y in range(2020, 2027)},
    **{y: 0.85 for y in range(2015, 2020)},
    **{y: 0.65 for y in range(2010, 2015)},
    **{y: 0.45 for y in range(2005, 2010)},
    **{y: 0.30 for y in range(2000, 2005)},
    **{y: 0.15 for y in range(1984, 2000)},
}
LIVENESS_DEFAULT: Final[float] = 0.15

# --- Card age decay -------------------------------------------------------
# Keyed by the card's OWN air year. Deliberately gentler than liveness: an old
# clue about a still-live topic is only slightly less useful (phrasing drifts),
# so this nudges rather than dominates.
CARD_AGE_WEIGHTS: Final[dict[int, float]] = {
    **{y: 1.00 for y in range(2020, 2027)},
    **{y: 0.95 for y in range(2015, 2020)},
    **{y: 0.90 for y in range(2010, 2015)},
    **{y: 0.80 for y in range(2000, 2010)},
    **{y: 0.70 for y in range(1984, 2000)},
}
CARD_AGE_DEFAULT: Final[float] = 0.70

# --- Archive (dead-card) thresholds --------------------------------------
# Cards matching these are moved to the Archive subdeck, never deleted. Cards
# you have already reviewed, and anything aired from ARCHIVE_PROTECT_YEAR on,
# are always exempt.
ARCHIVE_PROTECT_YEAR: Final[int] = 2020
ARCHIVE_STALE_BEFORE_YEAR: Final[int] = 2015  # time-anchored wording
ARCHIVE_ONEOFF_BEFORE_YEAR: Final[int] = 2010  # answer never repeated
ARCHIVE_DEAD_TOPIC_BEFORE_YEAR: Final[int] = 2005  # answer not seen since
DUPLICATE_JACCARD_THRESHOLD: Final[float] = 0.65
DUPLICATE_BLOCK_MAX_POSTINGS: Final[int] = 60  # skip common tokens when blocking

# Personal-weakness boost applied when ORDERING cards for study (not to the
# frequency score itself, which stays a pure game-likelihood measure).
WEAKNESS_PRIORITY_BOOST: Final[float] = 0.35

# TSV field names (from jwolle1 dataset)
# IMPORTANT: jwolle1 uses Jeopardy's native terminology, which is REVERSED from
# the Anki deck. In the TSV, `answer` is the clue/prompt shown to contestants
# and `question` is the correct response. In the Anki deck, field 9 "Question"
# is the clue shown and field 11 "Answer" is the response. So when mapping:
#   Anki Question (field 9)  <- TSV `answer`
#   Anki Answer   (field 11) <- TSV `question`
TSV_ROUND: Final[str] = "round"
TSV_CLUE_VALUE: Final[str] = "clue_value"
TSV_DAILY_DOUBLE_VALUE: Final[str] = "daily_double_value"
TSV_CATEGORY: Final[str] = "category"
TSV_COMMENTS: Final[str] = "comments"
TSV_ANSWER: Final[str] = "answer"  # the clue shown -> Anki Question field
TSV_QUESTION: Final[str] = "question"  # the response -> Anki Answer field
TSV_AIR_DATE: Final[str] = "air_date"
TSV_NOTES: Final[str] = "notes"

# TSV round codes -> Anki round names
ROUND_CODE_TO_NAME: Final[dict[str, str]] = {
    "1": "Jeopardy",
    "2": "Double Jeopardy",
    "3": "Final Jeopardy",
}

# Closed vocabulary of broad SUBJECTS for category classification.
# A controlled set so subject-level frequency rollups aggregate cleanly.
SUBJECTS: Final[tuple[str, ...]] = (
    "Literature",
    "History",
    "Geography",
    "Science",
    "Religion & Mythology",
    "Music",
    "Art",
    "Film & TV",
    "Sports",
    "Wordplay & Language",
    "Pop Culture",
    "Food & Drink",
    "People",
    "Politics & Government",
    "Business & Economics",
    "Nature & Animals",
    "Other",
)
SUBJECT_OTHER: Final[str] = "Other"

# Stake multipliers applied during frequency accumulation (multiplied with recency weight).
# Priority tiers (highest → lowest):
#   Final Jeopardy > DD (Double Jeopardy) > DD (Jeopardy)
#   > DJ by value (1.1–1.5) > J by value (0.6–1.0)
STAKE_FINAL_JEOPARDY: Final[float] = 4.0
STAKE_DD_DJ: Final[float] = 2.5  # Daily Double in Double Jeopardy
STAKE_DD_J: Final[float] = 2.0  # Daily Double in Jeopardy
STAKE_DJ_MIN: Final[float] = 1.1  # Double Jeopardy at $400
STAKE_DJ_MAX: Final[float] = 1.5  # Double Jeopardy at $2000
STAKE_DJ_VALUE_MIN: Final[int] = 400
STAKE_DJ_VALUE_MAX: Final[int] = 2000
STAKE_J_MIN: Final[float] = 0.6  # Jeopardy at $200
STAKE_J_MAX: Final[float] = 1.0  # Jeopardy at $1000
STAKE_J_VALUE_MIN: Final[int] = 200
STAKE_J_VALUE_MAX: Final[int] = 1000

# Study optimizer — ease factor tuning and performance tagging
EASE_BASE: Final[int] = 2500
EASE_MIN: Final[int] = 1300
EASE_MAX: Final[int] = 4000
# Penalty subtracted from EASE_BASE per freq tier (negative = bonus for rare cards)
FREQ_EASE_PENALTY: Final[dict[str, int]] = {
    "high": 600,
    "medium": 300,
    "low": 0,
    "rare": -200,
}
# Penalty subtracted from EASE_BASE per weakness tier
WEAKNESS_EASE_PENALTY: Final[dict[str, int]] = {"weak": 600, "medium": 300, "strong": 0}
PERF_WEAK_THRESHOLD: Final[float] = 0.60
PERF_STRONG_THRESHOLD: Final[float] = 0.80
# Bayesian prior for sparse categories (5 ghost reviews at 70% accuracy)
PRIOR_ACCURACY: Final[float] = 0.70
PRIOR_WEIGHT: Final[int] = 5
MIN_REVIEWS_FOR_CARD_PERF: Final[int] = 3
ANKI_COLLECTION_PATH: Final[str] = "~/.local/share/Anki2/User 1/collection.anki2"

# Classifier settings
CLASSIFY_BATCH_SIZE: Final[int] = 300
CLASSIFY_MODEL_DEFAULT: Final[str] = "haiku"
CLASSIFY_MAX_RETRIES: Final[int] = 5
CLASSIFY_RETRY_BACKOFF_SECS: Final[float] = 10.0
CLASSIFY_WORKERS: Final[int] = 2
TAXONOMY_PATH_DEFAULT: Final[str] = "category_taxonomy.json"
