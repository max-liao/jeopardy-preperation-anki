"""Per-card subjects for cards whose category names no subject.

jeopardy_taxonomy_helpers.py judges a whole CATEGORY from the answers of its cards.
That cannot help a grab-bag ("POTPOURRI"), whose five clues test five different
things, nor a category whose clues share a subject but whose answers point
elsewhere: "George Loveless was sent to this distant place in 1834" is a history
clue in a category whose answers (Australia, New Guinea, the Philippines) are
mostly places. Both kept showing "Frequently asked theme: Other".

This module judges one CARD from two independent signals:

1. The clue's words: the naive Bayes clue model built in
   jeopardy_taxonomy_helpers.py, applied to this clue alone.
2. The answer: the share of the classified categories the answer appears in that
   belong to each subject. "Australia" votes for Geography, which is why the answer
   alone would misfile the Loveless card.

The two are added as a product of experts (see CARD_SUBJECT_ANSWER_WEIGHT). The best
subject is shown only when it leads the runner-up by enough (a card with no
evidence stays "Other"), and never when the clue itself reads as wordplay. As in
the category rules, everything here is pure and deterministic.

A card's own subject changes only what the card shows. Categories pinned in
MANUAL_OVERRIDES keep their label, and that includes the grab-bags pinned as
"Other" (POTPOURRI, HODGEPODGE...): a pin says the CATEGORY has no subject, which
is exactly when a card's own evidence is wanted. A category whose card-by-card
subjects were reviewed and rejected is listed in NO_CARD_SUBJECTS instead.
"""

import math
from collections import Counter
from collections.abc import Collection, Iterable, Mapping

from decks.trivia.jeopardy.jeopardy_consts import (
    CARD_SUBJECT_ANSWER_SMOOTHING,
    CARD_SUBJECT_ANSWER_WEIGHT,
    CARD_SUBJECT_MIN_GAP_BY_SOURCE,
    CARD_SUBJECT_NEVER_SHOWN,
    EVIDENCE_FLOAT_TOLERANCE,
    EVIDENCE_TARGET_SUBJECTS,
    SUBJECT_WORDPLAY,
)
from decks.trivia.jeopardy.jeopardy_taxonomy_helpers import (
    AnswerVotes,
    answer_shares,
    category_subject,
    clue_log_likelihood,
    clue_words,
)
from decks.trivia.jeopardy.jeopardy_types import CardSubject, CardText, ClueVocabulary, TaxonomyEntry


def card_source_subject(
    category: str, taxonomy: Mapping[str, TaxonomyEntry]
) -> str | None:
    """The category label a card is labeled from, if its category qualifies.

    Args:
        category: normalized (uppercased) on-air category
        taxonomy: category -> (subject, sub_category, secondary_subject)

    Returns:
        A key of CARD_SUBJECT_MIN_GAP_BY_SOURCE (a category missing from the
        taxonomy counts as "Other"), or None when the card keeps its category's
        subject
    """
    subject = category_subject(category, taxonomy)
    return subject if subject in CARD_SUBJECT_MIN_GAP_BY_SOURCE else None


def card_subject_scores(
    words: Mapping[str, int],
    answer_key: str,
    votes: AnswerVotes,
    vocabulary: ClueVocabulary,
    subjects: Iterable[str],
) -> dict[str, float]:
    """Score each subject for one card: what the clue and the answer both say.

    Args:
        words: word -> occurrences in the card's clue
        answer_key: the card's normalized answer ("" if unusable)
        votes: output of `build_answer_votes`
        vocabulary: output of `build_clue_vocabulary`
        subjects: the subjects to score

    Returns:
        subject -> clue log-likelihood + CARD_SUBJECT_ANSWER_WEIGHT * ln(the
        answer's share of that subject + CARD_SUBJECT_ANSWER_SMOOTHING). An answer
        with too few votes to judge has no shares, which shifts every subject
        alike.
    """
    shares = answer_shares(votes.get(answer_key, {}))
    return {
        subject: clue_log_likelihood(words, subject, vocabulary)
        + CARD_SUBJECT_ANSWER_WEIGHT
        * math.log(shares.get(subject, 0.0) + CARD_SUBJECT_ANSWER_SMOOTHING)
        for subject in subjects
    }


def label_card(
    card: CardText,
    source: str,
    votes: AnswerVotes,
    vocabulary: ClueVocabulary,
) -> CardSubject | None:
    """The subject a card's own clue and answer agree on, if they are decisive.

    Subjects with no clue words at all are skipped, for the same reason
    `clue_subject` skips them: smoothing would rate every word alike for them,
    which is not evidence of anything.

    Args:
        card: the card to label
        source: the card's category label, a key of CARD_SUBJECT_MIN_GAP_BY_SOURCE
        votes: output of `build_answer_votes`
        vocabulary: output of `build_clue_vocabulary`

    Returns:
        The label, or None when the best subject is one that is never shown
        (CARD_SUBJECT_NEVER_SHOWN), does not lead the runner-up by
        CARD_SUBJECT_MIN_GAP_BY_SOURCE[source], or the clue's words read as
        wordplay more than as the best subject
    """
    subjects = [s for s in EVIDENCE_TARGET_SUBJECTS if vocabulary.totals[s] > 0]
    if len(subjects) < 2:
        return None
    words = Counter(clue_words(card.clue))
    scores = card_subject_scores(words, card.answer_key, votes, vocabulary, subjects)
    best, runner_up = sorted(subjects, key=lambda subject: -scores[subject])[:2]
    gap = scores[best] - scores[runner_up]
    threshold = CARD_SUBJECT_MIN_GAP_BY_SOURCE[source]
    if best in CARD_SUBJECT_NEVER_SHOWN or gap < threshold - EVIDENCE_FLOAT_TOLERANCE:
        return None
    if vocabulary.totals[SUBJECT_WORDPLAY] > 0 and clue_log_likelihood(
        words, SUBJECT_WORDPLAY, vocabulary
    ) > clue_log_likelihood(words, best, vocabulary):
        return None
    return CardSubject(
        note_id=card.note_id,
        category=card.category,
        source_subject=source,
        subject=best,
        gap=gap,
        runner_up=runner_up,
    )


def label_cards(
    cards: Iterable[CardText],
    taxonomy: Mapping[str, TaxonomyEntry],
    exempt: Collection[str],
    votes: AnswerVotes,
    vocabulary: ClueVocabulary,
) -> list[CardSubject]:
    """Find the cards of subject-less categories whose own evidence is decisive.

    Args:
        cards: every Jeopardy card in the deck
        taxonomy: category -> (subject, sub_category, secondary_subject), after the
            category rules have run, so the moved categories vote
        exempt: categories whose cards keep their category's subject
            (NO_CARD_SUBJECTS)
        votes: output of `build_answer_votes`, built from `taxonomy`
        vocabulary: output of `build_clue_vocabulary`, built from `taxonomy`

    Returns:
        The labeled cards, in category then note order
    """
    labeled: list[CardSubject] = []
    for card in cards:
        source = card_source_subject(card.category, taxonomy)
        if source is None or card.category in exempt:
            continue
        label = label_card(card, source, votes, vocabulary)
        if label is not None:
            labeled.append(label)
    labeled.sort(key=lambda item: (item["category"], item["note_id"]))
    return labeled
