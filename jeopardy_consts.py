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
FREQ_DETAILS_FIELD_NAME: Final[str] = "Frequency Details"
# Reused verbatim from the existing Jeopardy fields (Arial / size 20 /
# {"media":[]}) — a valid Anki FieldConfig protobuf.
FREQ_FIELD_CONFIG_HEX: Final[str] = "1a05417269616c2014fa0f0c7b226d65646961223a5b5d7d"
FREQ_DETAILS_FIELD_CONFIG_HEX: Final[str] = FREQ_FIELD_CONFIG_HEX

# The detail line describes the latest five complete years in the collection.
THEME_WINDOW_YEARS: Final[int] = 5

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

# Anki round name (field 3) for the Final Jeopardy round. Its one clue/day is
# handled specially in study_optimizer.py: pulled out of the day's category
# group and interspersed across the new-card queue instead of being grouped.
ROUND_FINAL_JEOPARDY: Final[str] = "Final Jeopardy"

# TSV round codes -> Anki round names
ROUND_CODE_TO_NAME: Final[dict[str, str]] = {
    "1": "Jeopardy",
    "2": "Double Jeopardy",
    "3": ROUND_FINAL_JEOPARDY,
}

# Closed vocabulary of broad SUBJECTS for category classification.
# A controlled set so subject-level frequency rollups aggregate cleanly.
SUBJECT_LITERATURE: Final[str] = "Literature"
SUBJECT_WORDPLAY: Final[str] = "Wordplay & Language"
SUBJECTS: Final[tuple[str, ...]] = (
    SUBJECT_LITERATURE,
    "History",
    "Geography",
    "Science",
    "Religion & Mythology",
    "Music",
    "Art",
    "Film & TV",
    "Sports",
    SUBJECT_WORDPLAY,
    "Pop Culture",
    "Food & Drink",
    "People",
    "Politics & Government",
    "Business & Economics",
    "Nature & Animals",
    "Other",
)
SUBJECT_OTHER: Final[str] = "Other"
# The sub-category consolidate_taxonomy.py gives every catch-all (Miscellaneous,
# Potpourri, General…). Like smart_prep's "Miscellaneous" default for categories
# missing from the taxonomy, it marks the absence of a topic.
SUBCATEGORY_UNCLASSIFIED: Final[str] = "Unclassified"

# Short per-subject class codes, expanded back to full text by CSS in the card
# template (see BADGE_STYLE_BLOCK). Same trade as TIER_BADGE_CLASS: the code on
# the note costs ~4 bytes (~1.8MB across the deck) where the literal subject
# text plus markup would cost ~36 bytes (~16MB). SUBJECT_OTHER is deliberately
# absent — "Other" is a non-label, so those badges render as the bare score.
SUBJECT_BADGE_CLASS: Final[dict[str, str]] = {
    subject: f"s{index}"
    for index, subject in enumerate(s for s in SUBJECTS if s != SUBJECT_OTHER)
}

# Separator drawn between the score and the subject inside the badge pill.
# A literal MIDDLE DOT, not the CSS escape "\00b7": browsers ended that escape
# at "\00" (which resolves to U+0000 and is substituted with U+FFFD), leaving a
# stray "b7" in the badge. The template is UTF-8, so the character is safe.
BADGE_SEPARATOR: Final[str] = " · "

BADGE_STYLE_BLOCK: Final[str] = (
    f"<style>/*{BADGE_STYLE_MARKER}*/"
    ".fq{display:inline-block;margin:4px 0;padding:2px 10px;border-radius:12px;"
    "color:#fff;font-size:12px;font-weight:bold}"
    f".fq.h{{background:{TIER_BADGE_COLORS['high']}}}"
    f".fq.m{{background:{TIER_BADGE_COLORS['medium']}}}"
    f".fq.l{{background:{TIER_BADGE_COLORS['low']}}}"
    f".fq.r{{background:{TIER_BADGE_COLORS['rare']}}}"
    + "".join(
        f'.fq.{code}::after{{content:"{BADGE_SEPARATOR}{subject}"}}'
        for subject, code in SUBJECT_BADGE_CLASS.items()
    )
    + "</style>\n"
)

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

# --- Deck options (deck_config table) ------------------------------------
# Field numbers below are for the `config` BLOB column of the `deck_config`
# table, which holds a serialized `DeckConfig.Config` protobuf message (one
# row shared by every deck in this collection). Sourced directly from Anki
# 25.02.1's proto/anki/deck_config.proto — see configure_deck_options.py,
# which uses these to defer review/relearning cards behind new cards so a
# day's 5-card category group is never split up by a due review mid-burst.
DECKCONFIG_FIELD_NEW_CARD_GATHER_PRIORITY: Final[int] = 34
DECKCONFIG_FIELD_NEW_CARD_SORT_ORDER: Final[int] = 32
DECKCONFIG_FIELD_NEW_MIX: Final[int] = 30  # "New/review order" in deck options UI
DECKCONFIG_FIELD_INTERDAY_LEARNING_MIX: Final[int] = 31

# ReviewMix enum: 0=mix with reviews (default), 1=after reviews, 2=before reviews.
DECKCONFIG_REVIEW_MIX_BEFORE_REVIEWS: Final[int] = 2

# NewCardGatherPriority values under which cards are gathered in ascending
# `due`-position order (0=Deck, 1=LowestPosition) — required for the day-group
# block ordering study_optimizer.py writes into `due` to actually take effect.
# Every other value (random variants, or 2=HighestPosition which walks `due`
# *backwards*) would scramble or invert it.
DECKCONFIG_SAFE_GATHER_PRIORITIES: Final[frozenset[int]] = frozenset({0, 1})
# NewCardSortOrder values that don't reshuffle after gathering (0=Template,
# 1=NoSort — equivalent here since this notetype has a single template).
DECKCONFIG_SAFE_SORT_ORDERS: Final[frozenset[int]] = frozenset({0, 1})

# learn_steps / relearn_steps are `repeated float` (minutes per step).
DECKCONFIG_FIELD_LEARN_STEPS: Final[int] = 1
DECKCONFIG_FIELD_RELEARN_STEPS: Final[int] = 2
# Desired step timings (was 5/10 learn, 15 relearn).
DECKCONFIG_LEARN_STEPS_MINUTES: Final[tuple[float, ...]] = (10.0, 20.0)
DECKCONFIG_RELEARN_STEPS_MINUTES: Final[tuple[float, ...]] = (30.0,)

# Classifier settings
CLASSIFY_BATCH_SIZE: Final[int] = 300
CLASSIFY_MODEL_DEFAULT: Final[str] = "haiku"
CLASSIFY_MAX_RETRIES: Final[int] = 5
CLASSIFY_RETRY_BACKOFF_SECS: Final[float] = 10.0
CLASSIFY_WORKERS: Final[int] = 2
TAXONOMY_PATH_DEFAULT: Final[str] = "category_taxonomy.json"

# --- Evidence-based reclassification --------------------------------------
# classify_categories.py sees only a category's NAME, so a pun ("A NOVEL PASSAGE"),
# a letter game ('CAPITAL "C"') or a name it failed to echo back lands in Other or
# Wordplay & Language even when every card tests one subject. smart_prep.py
# therefore re-examines those categories using their CARDS: an answer belongs to a
# subject to the degree that, elsewhere in the deck, it appears in categories
# already classified under that subject (see jeopardy_taxonomy_helpers.py).
#
# Subjects the evidence may move a category INTO: every real subject. Wordplay &
# Language is reserved for categories whose content is language itself, which no
# answer evidence can establish, and "Other" is the absence of a subject.
EVIDENCE_TARGET_SUBJECTS: Final[tuple[str, ...]] = tuple(
    subject for subject in SUBJECTS if subject not in (SUBJECT_OTHER, SUBJECT_WORDPLAY)
)
# Subjects whose categories do not vote on where an answer belongs. Wordplay
# categories recycle answers from every domain (a novel's title turns up in a
# rhyme-time category just as easily as in a books one), so their votes are
# noise; "Other" categories are the ones being re-examined.
EVIDENCE_NON_VOTING_SUBJECTS: Final[frozenset[str]] = frozenset(
    {SUBJECT_WORDPLAY, SUBJECT_OTHER}
)
# An answer seen in fewer classified categories than this is an anecdote, not
# evidence, and contributes nothing.
EVIDENCE_MIN_ANSWER_VOTES: Final[int] = 2
# Categories with fewer usable cards than this are too thin to judge.
EVIDENCE_MIN_CATEGORY_NOTES: Final[int] = 3
# Which name-only labels are re-examined, and how much answer evidence it takes to
# overrule each: the best subject's mean share across the category's cards.
# "Other" means the name told the classifier nothing. "Wordplay & Language" means
# it saw a word game, and for some of those the language IS the content (idioms,
# double meanings), so it takes more. A real subject is re-checked at the same
# bar, because puns fool the name-only pass there too ("CAPITALISM", filed under
# Business, is five world capitals). Every cut was calibrated by reading every
# move it makes (see JEOPARDY_PREP_DECK.md).
EVIDENCE_MIN_MEAN_SHARE_LABELED: Final[float] = 0.5
EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE: Final[dict[str, float]] = {
    **{
        subject: EVIDENCE_MIN_MEAN_SHARE_LABELED for subject in EVIDENCE_TARGET_SUBJECTS
    },
    SUBJECT_OTHER: 0.45,
    SUBJECT_WORDPLAY: 0.5,
}
# The best subject must also lead the runner-up by this much, so a category whose
# answers are split between, say, History and Geography stays where it is.
EVIDENCE_MIN_MARGIN: Final[float] = 0.2
# Absorbs float rounding in the two comparisons above (0.6 - 0.4 is
# 0.19999999999999996, which must still count as a margin of 0.2).
EVIDENCE_FLOAT_TOLERANCE: Final[float] = 1e-9
# Second, independent signal: a naive Bayes model of clue WORDS per subject,
# learned from every classified category (Wordplay included). A move needs it to
# name the same subject as the answers. It catches answers that belong to another
# medium (RUSSIAN OPERA: literary works, operatic clues) and word games whose
# answers are domain nouns (a body-part vocabulary category reads as Wordplay).
EVIDENCE_CLUE_SUBJECTS: Final[tuple[str, ...]] = EVIDENCE_TARGET_SUBJECTS + (
    SUBJECT_WORDPLAY,
)
EVIDENCE_CLUE_WORD_PATTERN: Final[str] = r"[a-z][a-z']+"  # on casefolded clue text
EVIDENCE_CLUE_SMOOTHING: Final[float] = 1.0  # add-one (Laplace) word smoothing
# How many reclassified categories smart_prep.py lists in its log (largest first).
EVIDENCE_REPORT_TOP_N: Final[int] = 15
# Header of the full move list written by `smart_prep.py --evidence-report PATH`.
EVIDENCE_REPORT_COLUMNS: Final[tuple[str, ...]] = (
    "category",
    "cards",
    "from",
    "to",
    "share",
    "runner_up",
    "runner_up_share",
)
