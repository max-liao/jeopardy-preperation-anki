"""Type definitions for Jeopardy collection processing."""

from typing import Literal, TypedDict


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
