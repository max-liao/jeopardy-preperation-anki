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

# Classifier settings
CLASSIFY_BATCH_SIZE: Final[int] = 300
CLASSIFY_MODEL_DEFAULT: Final[str] = "haiku"
CLASSIFY_MAX_RETRIES: Final[int] = 5
CLASSIFY_RETRY_BACKOFF_SECS: Final[float] = 10.0
CLASSIFY_WORKERS: Final[int] = 2
TAXONOMY_PATH_DEFAULT: Final[str] = "category_taxonomy.json"
