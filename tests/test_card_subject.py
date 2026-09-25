"""Tests for per-card subjects: a card's own clue and answer decide what it shows.

The fixture deck has classified categories for four subjects (each with a clue
vocabulary of its own, as in the real deck) and one wordplay category. Answers are
shared across categories the way real ones are: "australia" appears in two
Geography categories and one History category.
"""

import math
import unittest
from unittest import mock

from jeopardy_card_helpers import (
    card_source_subject,
    card_subject_scores,
    label_card,
    label_cards,
)
from jeopardy_consts import (
    CARD_SUBJECT_ANSWER_SMOOTHING,
    CARD_SUBJECT_ANSWER_WEIGHT,
    CARD_SUBJECT_MIN_GAP_BY_SOURCE,
    CARD_SUBJECT_NEVER_SHOWN,
    EVIDENCE_FLOAT_TOLERANCE,
    SUBJECT_OTHER,
    SUBJECT_WORDPLAY,
)
from jeopardy_taxonomy_helpers import (
    build_answer_votes,
    build_clue_vocabulary,
    category_subject,
    clue_log_likelihood,
    clue_words,
    group_cards_by_category,
)
from jeopardy_types import CardText, ClueVocabulary, TaxonomyEntry

LITERATURE = "Literature"
HISTORY = "History"
GEOGRAPHY = "Geography"
MUSIC = "Music"
POP_CULTURE = "Pop Culture"

LITERARY_CLUE = "This novel by the author has a title character in the book"
HISTORY_CLUE = (
    "This war ended when the king signed the treaty in the year of the battle"
)
GEOGRAPHY_CLUE = "This country lies on the continent with a capital city near the coast"
MUSIC_CLUE = "This opera by the composer has an aria sung by the tenor"
POP_CLUE = (
    "This celebrity star made gossip news on the red carpet in the fashion magazine"
)
WORD_CLUE = "This word also has a meaning as a term for something else"
# Half history, half geography: neither subject can lead by much.
SPLIT_CLUE = (
    "This war ended when the king signed the treaty; the capital city lies on the coast"
)
NO_WORDS_CLUE = "<img src='clue.jpg'>"

# category -> (subject, answers, the clue on each of its cards)
CLASSIFIED: dict[str, tuple[str, list[str], str]] = {
    "NOVELS": (LITERATURE, ["wuthering heights", "dracula", "emma"], LITERARY_CLUE),
    "CLASSIC LIT": (
        LITERATURE,
        ["wuthering heights", "emma", "ulysses"],
        LITERARY_CLUE,
    ),
    "WARS": (HISTORY, ["waterloo", "australia", "versailles"], HISTORY_CLUE),
    "TREATIES": (HISTORY, ["versailles", "waterloo", "utrecht"], HISTORY_CLUE),
    "COUNTRIES": (GEOGRAPHY, ["australia", "egypt", "peru"], GEOGRAPHY_CLUE),
    "WORLD TRAVEL": (GEOGRAPHY, ["australia", "egypt", "chile"], GEOGRAPHY_CLUE),
    "OPERA": (MUSIC, ["tosca", "aida", "carmen"], MUSIC_CLUE),
    "OPERA STARS": (MUSIC, ["tosca", "carmen", "norma"], MUSIC_CLUE),
    "CELEBRITIES": (POP_CULTURE, ["cher", "madonna", "prince"], POP_CLUE),
    "GOSSIP": (POP_CULTURE, ["cher", "prince", "bono"], POP_CLUE),
    "DOUBLE MEANINGS": (SUBJECT_WORDPLAY, ["bat", "bark", "spring"], WORD_CLUE),
}


def entry(subject: str) -> TaxonomyEntry:
    return (subject, subject, "")


TAXONOMY: dict[str, TaxonomyEntry] = {
    category: entry(subject) for category, (subject, _a, _c) in CLASSIFIED.items()
}


def fixture_model() -> tuple[dict[str, dict[str, int]], ClueVocabulary]:
    """Answer votes and clue vocabulary, built the way smart_prep builds them."""
    answers = {c: list(a) for c, (_s, a, _cl) in CLASSIFIED.items()}
    clues = {c: [clue] * len(a) for c, (_s, a, clue) in CLASSIFIED.items()}
    votes = build_answer_votes(answers, TAXONOMY)
    return {k: dict(v) for k, v in votes.items()}, build_clue_vocabulary(
        clues, TAXONOMY
    )


def card(
    clue: str, answer: str, note_id: int = 1, category: str = "GRAB BAG"
) -> CardText:
    return CardText(note_id=note_id, category=category, clue=clue, answer_key=answer)


class CardSubjectScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.votes, self.vocabulary = fixture_model()
        self.subjects = [LITERATURE, HISTORY, GEOGRAPHY, MUSIC]

    def scores(self, clue: str, answer: str) -> dict[str, float]:
        words = dict.fromkeys(clue_words(clue), 1)
        return card_subject_scores(
            words, answer, self.votes, self.vocabulary, self.subjects
        )

    def test_the_answer_alone_points_to_geography_for_australia(self) -> None:
        scores = self.scores(NO_WORDS_CLUE, "australia")
        self.assertEqual(max(scores, key=lambda s: scores[s]), GEOGRAPHY)

    def test_the_clue_alone_points_to_history(self) -> None:
        scores = self.scores(HISTORY_CLUE, "no such answer")
        self.assertEqual(max(scores, key=lambda s: scores[s]), HISTORY)

    def test_the_answer_term_is_the_weighted_log_of_its_smoothed_share(self) -> None:
        # australia: 2 Geography votes + 1 History vote, out of 3.
        scores = self.scores(NO_WORDS_CLUE, "australia")
        expected_geography = CARD_SUBJECT_ANSWER_WEIGHT * math.log(
            2 / 3 + CARD_SUBJECT_ANSWER_SMOOTHING
        )
        expected_music = CARD_SUBJECT_ANSWER_WEIGHT * math.log(
            0.0 + CARD_SUBJECT_ANSWER_SMOOTHING
        )
        self.assertAlmostEqual(scores[GEOGRAPHY], expected_geography)
        self.assertAlmostEqual(scores[MUSIC], expected_music)

    def test_an_answer_with_one_vote_shifts_every_subject_alike(self) -> None:
        # "utrecht" appears in one classified category: an anecdote, not evidence.
        once = self.scores(HISTORY_CLUE, "utrecht")
        never = self.scores(HISTORY_CLUE, "no such answer")
        for subject in self.subjects:
            self.assertAlmostEqual(once[subject], never[subject])

    def test_the_clue_term_is_the_clue_log_likelihood(self) -> None:
        words = dict.fromkeys(clue_words(LITERARY_CLUE), 1)
        scores = self.scores(LITERARY_CLUE, "no such answer")
        no_answer_term = CARD_SUBJECT_ANSWER_WEIGHT * math.log(
            CARD_SUBJECT_ANSWER_SMOOTHING
        )
        for subject in self.subjects:
            with self.subTest(subject=subject):
                self.assertAlmostEqual(
                    scores[subject] - no_answer_term,
                    clue_log_likelihood(words, subject, self.vocabulary),
                )


class LabelCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.votes, self.vocabulary = fixture_model()

    def label(self, clue: str, answer: str) -> str | None:
        found = label_card(
            card(clue, answer), SUBJECT_OTHER, self.votes, self.vocabulary
        )
        return None if found is None else found["subject"]

    def test_a_history_clue_beats_an_answer_that_votes_geography(self) -> None:
        # The Loveless card: "sent to this distant place in 1834" -> Australia.
        self.assertEqual(self.label(HISTORY_CLUE, "australia"), HISTORY)

    def test_the_same_answer_with_a_geography_clue_is_geography(self) -> None:
        self.assertEqual(self.label(GEOGRAPHY_CLUE, "australia"), GEOGRAPHY)

    def test_the_clue_decides_between_subjects_that_share_an_answer(self) -> None:
        self.assertEqual(self.label(LITERARY_CLUE, "wuthering heights"), LITERATURE)
        self.assertEqual(self.label(MUSIC_CLUE, "tosca"), MUSIC)

    def test_a_unanimous_answer_can_decide_a_clue_with_no_words(self) -> None:
        self.assertEqual(self.label(NO_WORDS_CLUE, "tosca"), MUSIC)

    def test_a_split_answer_cannot_decide_a_clue_with_no_words(self) -> None:
        self.assertIsNone(self.label(NO_WORDS_CLUE, "australia"))

    def test_a_card_with_no_evidence_gets_no_subject(self) -> None:
        self.assertIsNone(self.label(NO_WORDS_CLUE, "no such answer"))

    def test_a_clue_split_between_two_subjects_gets_no_subject(self) -> None:
        self.assertIsNone(self.label(SPLIT_CLUE, "no such answer"))

    def test_a_subject_that_is_never_shown_gets_no_subject_however_clear(self) -> None:
        self.assertIn(POP_CULTURE, CARD_SUBJECT_NEVER_SHOWN)
        self.assertIsNone(self.label(POP_CLUE, "cher"))
        with mock.patch(
            "jeopardy_card_helpers.CARD_SUBJECT_NEVER_SHOWN", frozenset[str]()
        ):
            self.assertEqual(self.label(POP_CLUE, "cher"), POP_CULTURE)

    def test_a_clue_that_reads_as_wordplay_gets_no_subject(self) -> None:
        # The answer votes for Literature, but the words are a word game's.
        with mock.patch.dict(CARD_SUBJECT_MIN_GAP_BY_SOURCE, {SUBJECT_OTHER: 0.0}):
            self.assertIsNone(self.label(WORD_CLUE, "wuthering heights"))
            self.assertEqual(self.label(LITERARY_CLUE, "wuthering heights"), LITERATURE)

    def test_the_lead_is_reported_with_the_runner_up(self) -> None:
        found = label_card(
            card(HISTORY_CLUE, "australia", note_id=7, category="A CATEGORY"),
            SUBJECT_OTHER,
            self.votes,
            self.vocabulary,
        )
        assert found is not None
        self.assertEqual(found["note_id"], 7)
        self.assertEqual(found["category"], "A CATEGORY")
        self.assertEqual(found["source_subject"], SUBJECT_OTHER)
        self.assertEqual(found["subject"], HISTORY)
        self.assertNotEqual(found["runner_up"], HISTORY)
        self.assertGreaterEqual(
            found["gap"], CARD_SUBJECT_MIN_GAP_BY_SOURCE[SUBJECT_OTHER]
        )

    def test_the_threshold_is_inclusive(self) -> None:
        found = label_card(
            card(HISTORY_CLUE, "australia"), SUBJECT_OTHER, self.votes, self.vocabulary
        )
        assert found is not None
        with mock.patch.dict(
            CARD_SUBJECT_MIN_GAP_BY_SOURCE, {SUBJECT_OTHER: found["gap"]}
        ):
            self.assertEqual(self.label(HISTORY_CLUE, "australia"), HISTORY)
        with mock.patch.dict(
            CARD_SUBJECT_MIN_GAP_BY_SOURCE, {SUBJECT_OTHER: found["gap"] + 0.01}
        ):
            self.assertIsNone(self.label(HISTORY_CLUE, "australia"))

    def test_float_rounding_at_the_threshold_does_not_lose_a_label(self) -> None:
        found = label_card(
            card(HISTORY_CLUE, "australia"), SUBJECT_OTHER, self.votes, self.vocabulary
        )
        assert found is not None
        just_above = {SUBJECT_OTHER: found["gap"] + EVIDENCE_FLOAT_TOLERANCE / 2}
        with mock.patch.dict(CARD_SUBJECT_MIN_GAP_BY_SOURCE, just_above):
            self.assertEqual(self.label(HISTORY_CLUE, "australia"), HISTORY)

    def test_subjects_are_ranked_only_among_those_with_clue_words(self) -> None:
        only_history = build_clue_vocabulary(
            {"WARS": [HISTORY_CLUE]}, {"WARS": entry(HISTORY)}
        )
        self.assertIsNone(
            label_card(
                card(HISTORY_CLUE, "waterloo"), SUBJECT_OTHER, self.votes, only_history
            )
        )

    def test_words_no_subject_has_seen_do_not_read_as_wordplay(self) -> None:
        # With no wordplay vocabulary the check has nothing to compare against.
        # Unfamiliar words must not count as wordplay: the answer decides.
        taxonomy = {c: e for c, e in TAXONOMY.items() if e[0] != SUBJECT_WORDPLAY}
        clues = {
            c: [clue] * len(a)
            for c, (s, a, clue) in CLASSIFIED.items()
            if s != SUBJECT_WORDPLAY
        }
        vocabulary = build_clue_vocabulary(clues, taxonomy)
        found = label_card(
            card("zzz qqq yyy xxx", "tosca"), SUBJECT_OTHER, self.votes, vocabulary
        )
        assert found is not None
        self.assertEqual(found["subject"], MUSIC)

    def test_a_vocabulary_with_no_wordplay_words_skips_the_wordplay_check(self) -> None:
        taxonomy = {c: e for c, e in TAXONOMY.items() if e[0] != SUBJECT_WORDPLAY}
        clues = {
            c: [clue] * len(a)
            for c, (s, a, clue) in CLASSIFIED.items()
            if s != SUBJECT_WORDPLAY
        }
        vocabulary = build_clue_vocabulary(clues, taxonomy)
        found = label_card(
            card(HISTORY_CLUE, "australia"), SUBJECT_OTHER, self.votes, vocabulary
        )
        assert found is not None
        self.assertEqual(found["subject"], HISTORY)


class CardSourceSubjectTests(unittest.TestCase):
    def test_other_categories_qualify(self) -> None:
        taxonomy = {"POTPOURRI": entry(SUBJECT_OTHER)}
        self.assertEqual(card_source_subject("POTPOURRI", taxonomy), SUBJECT_OTHER)

    def test_a_category_missing_from_the_taxonomy_counts_as_other(self) -> None:
        self.assertEqual(card_source_subject("UNSEEN", {}), SUBJECT_OTHER)

    def test_real_subjects_and_wordplay_never_qualify(self) -> None:
        for subject in (LITERATURE, SUBJECT_WORDPLAY):
            with self.subTest(subject=subject):
                self.assertIsNone(
                    card_source_subject("CATEGORY", {"CATEGORY": entry(subject)})
                )

    def test_category_subject_defaults_to_other(self) -> None:
        self.assertEqual(category_subject("UNSEEN", {}), SUBJECT_OTHER)
        self.assertEqual(category_subject("NOVELS", TAXONOMY), LITERATURE)


class LabelCardsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.votes, self.vocabulary = fixture_model()
        self.taxonomy: dict[str, TaxonomyEntry] = {
            **TAXONOMY,
            "GRAB BAG": entry(SUBJECT_OTHER),
            "ANOTHER BAG": entry(SUBJECT_OTHER),
        }

    def labels(
        self, cards: list[CardText], exempt: frozenset[str] = frozenset()
    ) -> dict[int, str]:
        found = label_cards(cards, self.taxonomy, exempt, self.votes, self.vocabulary)
        return {item["note_id"]: item["subject"] for item in found}

    def test_each_card_of_a_grab_bag_gets_its_own_subject(self) -> None:
        cards = [
            card(LITERARY_CLUE, "wuthering heights", note_id=1),
            card(HISTORY_CLUE, "australia", note_id=2),
            card(GEOGRAPHY_CLUE, "egypt", note_id=3),
            card(MUSIC_CLUE, "tosca", note_id=4),
        ]
        self.assertEqual(
            self.labels(cards), {1: LITERATURE, 2: HISTORY, 3: GEOGRAPHY, 4: MUSIC}
        )

    def test_cards_the_evidence_cannot_place_stay_unlabeled(self) -> None:
        cards = [
            card(HISTORY_CLUE, "australia", note_id=1),
            card(NO_WORDS_CLUE, "no such answer", note_id=2),
            card(WORD_CLUE, "wuthering heights", note_id=3),
        ]
        self.assertEqual(self.labels(cards), {1: HISTORY})

    def test_only_cards_of_categories_named_other_are_labeled(self) -> None:
        cards = [
            card(HISTORY_CLUE, "australia", note_id=1, category="GRAB BAG"),
            card(HISTORY_CLUE, "australia", note_id=2, category="NOVELS"),
            card(HISTORY_CLUE, "australia", note_id=3, category="DOUBLE MEANINGS"),
            card(HISTORY_CLUE, "australia", note_id=4, category="NEVER SEEN"),
        ]
        self.assertEqual(self.labels(cards), {1: HISTORY, 4: HISTORY})

    def test_the_labels_come_out_in_category_then_note_order(self) -> None:
        cards = [
            card(HISTORY_CLUE, "australia", note_id=9, category="GRAB BAG"),
            card(HISTORY_CLUE, "australia", note_id=5, category="ANOTHER BAG"),
            card(HISTORY_CLUE, "australia", note_id=2, category="GRAB BAG"),
        ]
        found = label_cards(
            cards, self.taxonomy, frozenset(), self.votes, self.vocabulary
        )
        self.assertEqual(
            [(item["category"], item["note_id"]) for item in found],
            [("ANOTHER BAG", 5), ("GRAB BAG", 2), ("GRAB BAG", 9)],
        )

    def test_exempt_categories_keep_their_own_subject(self) -> None:
        cards = [
            card(HISTORY_CLUE, "australia", note_id=1, category="GRAB BAG"),
            card(HISTORY_CLUE, "australia", note_id=2, category="ANOTHER BAG"),
        ]
        self.assertEqual(
            self.labels(cards, exempt=frozenset({"GRAB BAG"})), {2: HISTORY}
        )

    def test_no_cards_means_no_labels(self) -> None:
        self.assertEqual(self.labels([]), {})


class GroupCardsByCategoryTests(unittest.TestCase):
    def test_answers_skip_unusable_keys_but_clues_keep_every_card(self) -> None:
        cards = [
            CardText(1, "A", "clue one", "answer one"),
            CardText(2, "A", "clue two", ""),
            CardText(3, "B", "clue three", "answer three"),
        ]
        answers, clues = group_cards_by_category(cards)
        self.assertEqual(answers, {"A": ["answer one"], "B": ["answer three"]})
        self.assertEqual(clues, {"A": ["clue one", "clue two"], "B": ["clue three"]})

    def test_a_category_of_only_unusable_answers_has_clues_but_no_answers(self) -> None:
        answers, clues = group_cards_by_category([CardText(1, "A", "clue", "")])
        self.assertEqual(answers, {})
        self.assertEqual(clues, {"A": ["clue"]})


if __name__ == "__main__":
    unittest.main()
