"""Evidence-based refinement of the category taxonomy.

`classify_categories.py` classifies a category from its NAME alone. That is
enough for "SHAKESPEARE", but not for a pun ("A NOVEL PASSAGE": five quotations
from famous novels), a letter game ('CAPITAL "C"': five world capitals) or a name
the LLM failed to echo back. Those land in Other or Wordplay & Language, or under
the subject the pun names ("CAPITALISM" in Business), so their cards show the
wrong "Frequently asked theme".

This module re-examines every category using its CARDS, with two independent
signals:

1. Answers. The deck is the knowledge base: an answer belongs to a subject to the
   degree that, elsewhere in the deck, it appears in categories classified under
   that subject. A category scores each subject by the mean of its answers'
   shares, not counting its own votes. The best subject must clear a bar that
   depends on what the name-only pass said, and beat the runner-up by a margin.
2. Clue vocabulary. A naive Bayes model of the words in each subject's clues,
   learned from every classified category. It must name the same subject, which
   keeps a category whose answers belong to another medium (an opera category
   about literary works) or to another sense of a word (a vocabulary game whose
   answers are body parts) where it is.

Everything here is pure and deterministic: same taxonomy and cards in, same
reclassifications out. Manual overrides are respected by the caller passing them
as `protected`.
"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence

from jeopardy_consts import (
    EVIDENCE_CLUE_SMOOTHING,
    EVIDENCE_CLUE_SUBJECTS,
    EVIDENCE_CLUE_WORD_PATTERN,
    EVIDENCE_FLOAT_TOLERANCE,
    EVIDENCE_MIN_ANSWER_VOTES,
    EVIDENCE_MIN_CATEGORY_NOTES,
    EVIDENCE_MIN_MARGIN,
    EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE,
    EVIDENCE_NON_VOTING_SUBJECTS,
    EVIDENCE_TARGET_SUBJECTS,
    SUBJECT_OTHER,
)
from jeopardy_db_helpers import strip_html_media
from jeopardy_types import (
    ClueVocabulary,
    EvidenceReclassification,
    SubjectEvidence,
    TaxonomyEntry,
)

# answer key -> subject -> number of distinct classified categories it appears in
AnswerVotes = Mapping[str, Mapping[str, int]]

_CLUE_WORD_RE = re.compile(EVIDENCE_CLUE_WORD_PATTERN)


def build_answer_votes(
    category_answers: Mapping[str, Sequence[str]],
    taxonomy: Mapping[str, TaxonomyEntry],
) -> AnswerVotes:
    """Count, per answer, the distinct categories it appears in, by subject.

    A category votes at most once per answer however many times it aired, so a
    perennial category cannot drown out the rest. Categories that are "Other"
    or Wordplay, or missing from the taxonomy, do not vote (see
    EVIDENCE_NON_VOTING_SUBJECTS).

    Args:
        category_answers: category -> normalized answer key of each of its cards
        taxonomy: category -> (subject, sub_category, secondary_subject)

    Returns:
        answer key -> subject -> distinct-category count
    """
    votes: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for category, answers in category_answers.items():
        entry = taxonomy.get(category)
        if entry is None or entry[0] in EVIDENCE_NON_VOTING_SUBJECTS:
            continue
        for answer in set(answers):
            votes[answer][entry[0]] += 1
    return votes


def answer_shares(
    subject_votes: Mapping[str, int], own_subject: str | None = None
) -> dict[str, float]:
    """Fraction of an answer's classified appearances that fall in each subject.

    Args:
        subject_votes: subject -> distinct-category count for one answer
        own_subject: the subject the category being judged voted for, if it
            voted: its own vote is taken out, so a label cannot confirm itself

    Returns:
        subject -> share (summing to 1.0), or {} when the answer has fewer than
        EVIDENCE_MIN_ANSWER_VOTES votes: a single appearance is an anecdote
    """
    counts = dict(subject_votes)
    if own_subject is not None and counts.get(own_subject, 0) > 0:
        counts[own_subject] -= 1
    total = sum(counts.values())
    if total < EVIDENCE_MIN_ANSWER_VOTES:
        return {}
    return {subject: count / total for subject, count in counts.items() if count}


def mean_subject_shares(
    answers: Sequence[str], votes: AnswerVotes, own_subject: str | None = None
) -> dict[str, float]:
    """Average each subject's share across a category's cards.

    Answers the deck has no evidence for count as 0.0 for every subject, which
    is deliberately conservative: unknown is not evidence.

    Args:
        answers: normalized answer key of each card in the category
        votes: output of `build_answer_votes`
        own_subject: the subject the category itself voted for, or None if it
            did not vote (see `answer_shares`)

    Returns:
        subject -> mean share, 0.0-1.0 (empty for an empty category)
    """
    if not answers:
        return {}
    sums: defaultdict[str, float] = defaultdict(float)
    for answer in answers:
        shares = answer_shares(votes.get(answer, {}), own_subject)
        for subject, share in shares.items():
            sums[subject] += share
    return {subject: total / len(answers) for subject, total in sums.items()}


def rank_subjects(shares: Mapping[str, float]) -> SubjectEvidence:
    """The best target subject and the runner-up (ties keep SUBJECTS order).

    Args:
        shares: output of `mean_subject_shares`

    Returns:
        The top two of EVIDENCE_TARGET_SUBJECTS by mean share
    """
    best, runner_up = sorted(
        EVIDENCE_TARGET_SUBJECTS, key=lambda subject: -shares.get(subject, 0.0)
    )[:2]
    return SubjectEvidence(
        subject=best,
        share=shares.get(best, 0.0),
        runner_up=runner_up,
        runner_up_share=shares.get(runner_up, 0.0),
    )


def is_decisive(evidence: SubjectEvidence, source_subject: str) -> bool:
    """True if the answers are strong enough to overrule the name-only label.

    Args:
        evidence: output of `rank_subjects`
        source_subject: the name-only label, a key of EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE

    Returns:
        Whether the best subject clears the bar for `source_subject` and leads
        the runner-up by at least EVIDENCE_MIN_MARGIN
    """
    threshold = EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE[source_subject]
    margin = evidence.share - evidence.runner_up_share
    return (
        evidence.share >= threshold - EVIDENCE_FLOAT_TOLERANCE
        and margin >= EVIDENCE_MIN_MARGIN - EVIDENCE_FLOAT_TOLERANCE
    )


def clue_words(clue: str) -> list[str]:
    """The words of a clue, casefolded, with HTML and sound references removed."""
    return _CLUE_WORD_RE.findall(strip_html_media(clue).casefold())


def build_clue_vocabulary(
    category_clues: Mapping[str, Sequence[str]],
    taxonomy: Mapping[str, TaxonomyEntry],
) -> ClueVocabulary:
    """Count the words in each subject's clues, over every classified category.

    Args:
        category_clues: category -> clue text of each of its cards
        taxonomy: category -> (subject, sub_category, secondary_subject)

    Returns:
        Per-subject word counts for EVIDENCE_CLUE_SUBJECTS ("Other" and
        categories missing from the taxonomy teach it nothing)
    """
    word_counts: dict[str, Counter[str]] = {
        subject: Counter() for subject in EVIDENCE_CLUE_SUBJECTS
    }
    for category, clues in category_clues.items():
        entry = taxonomy.get(category)
        if entry is None or entry[0] not in word_counts:
            continue
        counts = word_counts[entry[0]]
        for clue in clues:
            counts.update(clue_words(clue))
    vocabulary: set[str] = set()
    for counts in word_counts.values():
        vocabulary.update(counts)
    return ClueVocabulary(
        word_counts=word_counts,
        totals={subject: counts.total() for subject, counts in word_counts.items()},
        vocabulary_size=len(vocabulary),
    )


def clue_log_likelihood(
    words: Mapping[str, int], subject: str, vocabulary: ClueVocabulary
) -> float:
    """Log-probability of these word counts under one subject's clue vocabulary.

    Args:
        words: word -> occurrences in the clues being judged
        subject: one of EVIDENCE_CLUE_SUBJECTS
        vocabulary: output of `build_clue_vocabulary`

    Returns:
        Sum of log P(word | subject) with add-one smoothing
    """
    counts = vocabulary.word_counts[subject]
    denominator = (
        vocabulary.totals[subject]
        + EVIDENCE_CLUE_SMOOTHING * vocabulary.vocabulary_size
    )
    return sum(
        occurrences
        * math.log((counts.get(word, 0) + EVIDENCE_CLUE_SMOOTHING) / denominator)
        for word, occurrences in words.items()
    )


def clue_subject(clues: Iterable[str], vocabulary: ClueVocabulary) -> str | None:
    """The subject whose clue vocabulary best explains these clues.

    Every subject gets the same prior, so a big subject does not win by size.
    A subject with no clue words at all is skipped: smoothing would give it the
    same modest probability for every word, which is not evidence of anything.

    Args:
        clues: clue text of each card in a category
        vocabulary: output of `build_clue_vocabulary`

    Returns:
        One of EVIDENCE_CLUE_SUBJECTS (ties keep that order), or None when the
        clues contain no words, or no subject has any, and so nothing can be
        confirmed
    """
    words = Counter(word for clue in clues for word in clue_words(clue))
    subjects = [s for s in EVIDENCE_CLUE_SUBJECTS if vocabulary.totals[s] > 0]
    if not words or not subjects:
        return None
    return max(
        subjects, key=lambda subject: clue_log_likelihood(words, subject, vocabulary)
    )


def source_subject(
    category: str,
    taxonomy: Mapping[str, TaxonomyEntry],
    protected: Collection[str],
) -> str | None:
    """The name-only label a category is re-examined from, if it is a candidate.

    Args:
        category: normalized (uppercased) on-air category
        taxonomy: category -> (subject, sub_category, secondary_subject)
        protected: categories a human has pinned (MANUAL_OVERRIDES); never moved

    Returns:
        A key of EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE (a category missing from the
        taxonomy counts as "Other"), or None when the category keeps its label
    """
    if category in protected:
        return None
    entry = taxonomy.get(category)
    subject = SUBJECT_OTHER if entry is None else entry[0]
    return subject if subject in EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE else None


def find_reclassifications(
    category_answers: Mapping[str, Sequence[str]],
    category_clues: Mapping[str, Sequence[str]],
    taxonomy: Mapping[str, TaxonomyEntry],
    protected: Collection[str],
) -> list[EvidenceReclassification]:
    """Find candidate categories whose answers AND clue words agree on a subject.

    Args:
        category_answers: category -> normalized answer key of each of its cards
        category_clues: category -> clue text of each of its cards
        taxonomy: category -> (subject, sub_category, secondary_subject)
        protected: categories a human has pinned; never moved

    Returns:
        The categories that qualify, largest first (ties broken by name)
    """
    votes = build_answer_votes(category_answers, taxonomy)
    vocabulary = build_clue_vocabulary(category_clues, taxonomy)
    found: list[EvidenceReclassification] = []
    for category, answers in category_answers.items():
        if len(answers) < EVIDENCE_MIN_CATEGORY_NOTES:
            continue
        source = source_subject(category, taxonomy, protected)
        if source is None:
            continue
        # A category with a real subject voted for it (see build_answer_votes).
        own_vote = None if source in EVIDENCE_NON_VOTING_SUBJECTS else source
        evidence = rank_subjects(mean_subject_shares(answers, votes, own_vote))
        if evidence.subject == source or not is_decisive(evidence, source):
            continue
        if (
            clue_subject(category_clues.get(category, ()), vocabulary)
            != evidence.subject
        ):
            continue
        found.append(
            EvidenceReclassification(
                category=category,
                notes=len(answers),
                source_subject=source,
                subject=evidence.subject,
                mean_share=evidence.share,
                runner_up=evidence.runner_up,
                runner_up_share=evidence.runner_up_share,
            )
        )
    found.sort(key=lambda item: (-item["notes"], item["category"]))
    return found


def apply_reclassifications(
    taxonomy: Mapping[str, TaxonomyEntry],
    reclassifications: Sequence[EvidenceReclassification],
) -> dict[str, TaxonomyEntry]:
    """Return a copy of the taxonomy with each category moved to its new subject.

    The sub-category becomes the subject's own name ("Geography"): the one the
    name-only pass attached ("Letter Puns", "Card Games" on an "Other" category,
    "Bankruptcy" on the Business pun "CHAPTER 11") would contradict the new
    subject, and the evidence says which subject the category is, not which
    topic within it. A secondary subject is kept unless it would now duplicate
    the primary.

    Args:
        taxonomy: category -> (subject, sub_category, secondary_subject)
        reclassifications: output of `find_reclassifications`

    Returns:
        A new dict; the input is never mutated
    """
    refined = dict(taxonomy)
    for item in reclassifications:
        category, subject = item["category"], item["subject"]
        secondary = taxonomy.get(category, (SUBJECT_OTHER, "", ""))[2]
        refined[category] = (
            subject,
            subject,
            "" if secondary == subject else secondary,
        )
    return refined


def reclassify_by_evidence(
    taxonomy: Mapping[str, TaxonomyEntry],
    category_answers: Mapping[str, Sequence[str]],
    category_clues: Mapping[str, Sequence[str]],
    protected: Collection[str],
) -> tuple[dict[str, TaxonomyEntry], list[EvidenceReclassification]]:
    """Move each category to the subject its cards point to, when they agree.

    Args:
        taxonomy: category -> (subject, sub_category, secondary_subject)
        category_answers: category -> normalized answer key of each of its cards
        category_clues: category -> clue text of each of its cards
        protected: categories a human has pinned (MANUAL_OVERRIDES); never moved

    Returns:
        (refined taxonomy, the reclassifications that were applied)
    """
    found = find_reclassifications(
        category_answers, category_clues, taxonomy, protected
    )
    return apply_reclassifications(taxonomy, found), found
