"""Type definitions for Jeopardy collection processing."""

from collections.abc import Mapping
from typing import Literal, NamedTuple, TypedDict


class AnswerFrequency(TypedDict):
    """Statistics for a unique answer across all occurrences."""

    answer_text: str
    total_count: int
    year_distribution: dict[int, int]
    time_weighted_score: float
    frequency_tier: Literal["high", "medium", "low", "rare"]


class CardTags(TypedDict):
    """Tags to apply to a single card."""

    frequency: str  # "freq:high", "freq:medium", "freq:low", or "freq:rare"
    category: str  # "cat:CATEGORY_NAME"
    era: str  # "era:recent", "era:modern", or "era:old"


class CategoryClassification(TypedDict):
    """LLM classification of a single on-air category."""

    category: str  # normalized (uppercased) on-air category text
    subject: str  # broad bucket from the controlled SUBJECTS vocabulary
    sub_category: str  # normalized, human-readable narrower grouping
    # Non-empty when the category uses a wordplay format (Before & After, Rhyme
    # Time, Anagrams…) to test knowledge of a specific non-language domain.
    # E.g. "SCIENCE BEFORE & AFTER" → subject="Wordplay & Language",
    # secondary_subject="Science". Used in blended scoring so the card gets
    # credit in both its format domain and its knowledge domain.
    secondary_subject: str  # "" if purely wordplay / no secondary domain


# One category's classification as smart_prep.py consumes it:
# (subject, sub_category, secondary_subject).
TaxonomyEntry = tuple[str, str, str]


class EvidenceReclassification(TypedDict):
    """A category moved to a real subject because its cards point there."""

    category: str  # normalized (uppercased) on-air category text
    notes: int  # cards in the category that carry a usable answer
    source_subject: str  # the name-only label: "Other" or "Wordplay & Language"
    subject: str  # the subject the evidence moved it to
    mean_share: float  # mean answer share of `subject` across the cards, 0.0-1.0
    runner_up: str  # the next-best subject by answer share
    runner_up_share: float  # its mean share, 0.0-1.0


class CardText(NamedTuple):
    """What the evidence rules read from one Jeopardy note."""

    note_id: int
    category: str  # normalized (uppercased) on-air category
    clue: str  # the clue shown, as stored (may hold HTML and media references)
    answer_key: str  # normalized answer key; "" when the answer is unusable


class CardSubject(TypedDict):
    """A card shown under a subject of its own, whatever its category is."""

    note_id: int
    category: str  # normalized (uppercased) on-air category
    source_subject: str  # the category's subject: "Other"
    subject: str  # the subject the card's own clue and answer point to
    gap: float  # the subject's lead over the runner-up, in nats of score
    runner_up: str  # the next-best subject


class SubjectEvidence(NamedTuple):
    """What a category's answers say: the best subject and its runner-up."""

    subject: str
    share: float  # mean answer share, 0.0-1.0
    runner_up: str
    runner_up_share: float


class ClueVocabulary(NamedTuple):
    """Clue-word counts per subject: a naive Bayes model learned from the deck."""

    word_counts: Mapping[str, Mapping[str, int]]  # subject -> word -> occurrences
    totals: Mapping[str, int]  # subject -> words seen in its clues
    vocabulary_size: int  # distinct words across every subject


class NoteRow(TypedDict):
    """A single note row from the Anki database."""

    id: int
    guid: str
    mid: int
    mod: int
    usn: int
    tags: str
    flds: str


class AnkiCardRow(TypedDict):
    """A single card row from the Anki database."""

    id: int
    nid: int
    did: int
    mod: int
    usn: int
    type: int
    queue: int
    due: int
    ivl: int
    factor: int
    reps: int
    lapses: int
    left: int
    odue: int
    odid: int
    flags: int
    data: str
