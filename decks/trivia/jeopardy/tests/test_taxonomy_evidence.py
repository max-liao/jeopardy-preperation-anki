"""Tests for evidence-based reclassification of categories.

The fixture deck mirrors the real one: novel titles that are also films (so their
votes are split), letter games whose answers are world capitals, word games whose
answers are body parts, an opera category whose answers are literary works, a
books category the name-only pass filed under Film & TV, and grab-bags and
correctly labelled categories that must NOT move. Each classified category's clues use its subject's
vocabulary, which is what the clue check learns from.
"""

import math
import unittest
from collections.abc import Collection

from decks.trivia.jeopardy.jeopardy_consts import (
    EVIDENCE_CLUE_SUBJECTS,
    EVIDENCE_MIN_MARGIN,
    EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE,
    EVIDENCE_MIN_MEAN_SHARE_LABELED,
    SUBJECT_OTHER,
    SUBJECT_WORDPLAY,
)
from decks.trivia.jeopardy.jeopardy_taxonomy_helpers import (
    answer_shares,
    build_answer_votes,
    build_clue_vocabulary,
    clue_log_likelihood,
    clue_subject,
    clue_words,
    is_decisive,
    mean_subject_shares,
    rank_subjects,
    reclassify_by_evidence,
    source_subject,
)
from decks.trivia.jeopardy.jeopardy_types import EvidenceReclassification, SubjectEvidence, TaxonomyEntry

LITERATURE: TaxonomyEntry = ("Literature", "Novels", "")
FILM: TaxonomyEntry = ("Film & TV", "Movies", "")
GEOGRAPHY: TaxonomyEntry = ("Geography", "Capital Cities", "")
MUSIC: TaxonomyEntry = ("Music", "Opera", "")
SCIENCE: TaxonomyEntry = ("Science", "Anatomy", "")
WORDPLAY: TaxonomyEntry = ("Wordplay & Language", "Wordplay", "")
LETTER_PUNS: TaxonomyEntry = ("Wordplay & Language", "Letter Puns", "")
OTHER: TaxonomyEntry = ("Other", "Unclassified", "")


def moved_to(subject: str, secondary: str = "") -> TaxonomyEntry:
    """What a moved category looks like: the subject is also its sub-category."""
    return (subject, subject, secondary)


# The five answers of the real "A NOVEL PASSAGE" category (each clue quotes the
# novel), as normalized answer keys. "One Flew Over the Cuckoo's Nest" is also a
# film, so its votes are split 50/50 below.
NOVELS = [
    "three musketeers",
    "gulliver's travels",
    "one flew over the cuckoo's nest",
    "wuthering heights",
    "stranger",
]
FILMS = ["casablanca", "citizen kane", "rocky", "one flew over the cuckoo's nest"]
C_CAPITALS = ["copenhagen", "cape town", "cairo", "caracas", "canberra"]
A_CAPITALS = ["andorra", "annapolis", "albany", "adelaide", "accra"]
INDIA = ["himalayas", "taj mahal", "ganges", "delhi"]
# Literary works that are also operas: RUSSIAN OPERA's answers vote Literature.
RUSSIAN_WORKS = ["war and peace", "lady macbeth", "eugene onegin", "boris godunov"]
BODY_PARTS = ["nose", "tongue", "hip", "rib", "shin"]
# George Eliot novels, asked in two books categories and in BOOK CLUB, which the
# name-only pass filed under Film & TV.
ELIOT = ["middlemarch", "adam bede", "silas marner"]
GRAB_BAG = ["casablanca", "himalayas", "wuthering heights", "rocky", "taj mahal"]

# One clue per card, in the vocabulary of the category's subject. Every subject
# gets roughly the same number of clue words, as in the real deck, where each has
# hundreds of thousands and no subject wins merely by being small.
LITERARY_CLUE = "This novel by the author has a title character in the book"
FILM_CLUE = "This film starred the actor & the movie director won an Oscar"
CAPITAL_CLUE = "This capital city of the country lies on a river"
OPERA_CLUE = "This opera by the composer has an aria sung by the tenor"
ANATOMY_CLUE = "This organ of the body sits near a bone & a muscle"
WORD_CLUE = "This word also has a meaning as a term for something else"
SPELLING_CLUE = "Spell this word, a synonym with another meaning, letter by letter"

Reclassification = tuple[dict[str, TaxonomyEntry], list[EvidenceReclassification]]


class ReclassifyByEvidenceTests(unittest.TestCase):
    """The behaviour on a small deck shaped like the real one."""

    def setUp(self) -> None:
        self.taxonomy: dict[str, TaxonomyEntry] = {
            "NOVELS": LITERATURE,
            "CLASSIC LIT": LITERATURE,
            "RUSSIAN NOVELS": LITERATURE,
            "RUSSIAN AUTHORS": LITERATURE,
            "MOVIES": FILM,
            "CINEMA CLASSICS": FILM,
            "WORLD CAPITALS": GEOGRAPHY,
            "CAPITAL CITIES": GEOGRAPHY,
            "ASIA": GEOGRAPHY,
            "WORLD TRAVEL": GEOGRAPHY,
            "FILM FACTS": FILM,
            "OPERA": MUSIC,
            "OPERA STARS": MUSIC,
            "COMPOSERS": MUSIC,
            "CLASSICAL MUSIC": MUSIC,
            "THE BODY HUMAN": SCIENCE,
            "ANATOMY": SCIENCE,
            "MEDICINE": SCIENCE,
            "DOUBLE MEANINGS": WORDPLAY,
            "RHYME TIME": WORDPLAY,
            "VOCABULARY": WORDPLAY,
            "SPELLING BEE": WORDPLAY,
            "HOMOPHONES": WORDPLAY,
            "A NOVEL PASSAGE": OTHER,
            'CAPITAL "C\\"': OTHER,
            'CAPITAL "A"': LETTER_PUNS,
            "CLASSIC CINEMA": OTHER,
            "A PASSAGE TO INDIA": OTHER,
            "POTPOURRI": OTHER,
            "RUSSIAN OPERA": OTHER,
            "AN ANATOMY OF WORDS": WORDPLAY,
            "GEORGE ELIOT": LITERATURE,
            "VICTORIAN NOVELS": LITERATURE,
            "BOOK CLUB": FILM,
        }
        self.category_answers: dict[str, list[str]] = {
            "NOVELS": NOVELS,
            "CLASSIC LIT": NOVELS,
            "RUSSIAN NOVELS": RUSSIAN_WORKS,
            "RUSSIAN AUTHORS": RUSSIAN_WORKS,
            "MOVIES": FILMS,
            "CINEMA CLASSICS": FILMS,
            "WORLD CAPITALS": C_CAPITALS + A_CAPITALS,
            "CAPITAL CITIES": C_CAPITALS + A_CAPITALS,
            "ASIA": INDIA,
            "WORLD TRAVEL": INDIA,
            "FILM FACTS": ["jaws", "psycho", "vertigo", "alien"],
            "OPERA": ["la boheme", "carmen", "aida"],
            "OPERA STARS": ["pavarotti", "callas", "domingo", "caruso"],
            "COMPOSERS": ["verdi", "puccini", "mozart", "wagner"],
            "CLASSICAL MUSIC": ["bolero", "messiah", "requiem", "the planets"],
            "THE BODY HUMAN": BODY_PARTS,
            "ANATOMY": BODY_PARTS,
            "MEDICINE": ["aspirin", "insulin", "penicillin", "vaccine"],
            "DOUBLE MEANINGS": ["bat", "bark", "pitch"],
            "RHYME TIME": ["wuthering heights", "himalayas", "rocky"],
            "VOCABULARY": ["loquacious", "ephemeral", "quixotic", "ubiquitous"],
            "SPELLING BEE": ["onomatopoeia", "rhythm", "necessary", "separate"],
            "HOMOPHONES": ["pair & pear", "flour & flower", "knight & night"],
            "A NOVEL PASSAGE": NOVELS,
            'CAPITAL "C\\"': C_CAPITALS,
            'CAPITAL "A"': A_CAPITALS,
            "CLASSIC CINEMA": FILMS,
            "A PASSAGE TO INDIA": INDIA,
            "POTPOURRI": GRAB_BAG,
            "RUSSIAN OPERA": RUSSIAN_WORKS,
            "AN ANATOMY OF WORDS": BODY_PARTS,
            "GEORGE ELIOT": ELIOT,
            "VICTORIAN NOVELS": ELIOT,
            "BOOK CLUB": ELIOT,
        }
        clue_for_category = {
            "NOVELS": LITERARY_CLUE,
            "CLASSIC LIT": LITERARY_CLUE,
            "RUSSIAN NOVELS": LITERARY_CLUE,
            "RUSSIAN AUTHORS": LITERARY_CLUE,
            "MOVIES": FILM_CLUE,
            "CINEMA CLASSICS": FILM_CLUE,
            "WORLD CAPITALS": CAPITAL_CLUE,
            "CAPITAL CITIES": CAPITAL_CLUE,
            "ASIA": CAPITAL_CLUE,
            "WORLD TRAVEL": CAPITAL_CLUE,
            "FILM FACTS": FILM_CLUE,
            "OPERA": OPERA_CLUE,
            "OPERA STARS": OPERA_CLUE,
            "COMPOSERS": OPERA_CLUE,
            "CLASSICAL MUSIC": OPERA_CLUE,
            "THE BODY HUMAN": ANATOMY_CLUE,
            "ANATOMY": ANATOMY_CLUE,
            "MEDICINE": ANATOMY_CLUE,
            "DOUBLE MEANINGS": WORD_CLUE,
            "RHYME TIME": WORD_CLUE,
            "VOCABULARY": WORD_CLUE,
            "SPELLING BEE": SPELLING_CLUE,
            "HOMOPHONES": SPELLING_CLUE,
            "A NOVEL PASSAGE": "From the book: this novel's title character says all for one",
            'CAPITAL "C\\"': "This capital lies on the banks of the world's longest river",
            'CAPITAL "A"': "This capital city lies on a river in the country",
            "CLASSIC CINEMA": FILM_CLUE,
            "A PASSAGE TO INDIA": CAPITAL_CLUE,
            "POTPOURRI": "This is a little bit of everything",
            "RUSSIAN OPERA": OPERA_CLUE,
            "AN ANATOMY OF WORDS": WORD_CLUE,
            "GEORGE ELIOT": LITERARY_CLUE,
            "VICTORIAN NOVELS": LITERARY_CLUE,
            "BOOK CLUB": LITERARY_CLUE,
        }
        self.category_clues: dict[str, list[str]] = {
            category: [clue] * len(self.category_answers[category])
            for category, clue in clue_for_category.items()
        }

    def reclassify(self, protected: Collection[str] = ()) -> Reclassification:
        return reclassify_by_evidence(
            self.taxonomy, self.category_answers, self.category_clues, protected
        )

    def moved(self) -> dict[str, str]:
        """category -> new subject, for every reclassification."""
        return {item["category"]: item["subject"] for item in self.reclassify()[1]}

    def test_a_novel_passage_moves_to_literature(self) -> None:
        refined, found = self.reclassify()
        self.assertEqual(refined["A NOVEL PASSAGE"], moved_to("Literature"))
        item = next(i for i in found if i["category"] == "A NOVEL PASSAGE")
        self.assertEqual(item["notes"], len(NOVELS))
        self.assertEqual(item["source_subject"], SUBJECT_OTHER)

    def test_capital_c_moves_to_geography(self) -> None:
        refined, _ = self.reclassify()
        self.assertEqual(refined['CAPITAL "C\\"'], moved_to("Geography"))

    def test_letter_game_labelled_wordplay_moves_to_its_content(self) -> None:
        refined, found = self.reclassify()
        self.assertEqual(refined['CAPITAL "A"'], moved_to("Geography"))
        item = next(i for i in found if i["category"] == 'CAPITAL "A"')
        self.assertEqual(item["source_subject"], SUBJECT_WORDPLAY)

    def test_film_and_geography_categories_move_to_their_subjects(self) -> None:
        moved = self.moved()
        self.assertEqual(moved["CLASSIC CINEMA"], "Film & TV")
        self.assertEqual(moved["A PASSAGE TO INDIA"], "Geography")

    def test_grab_bag_stays_other(self) -> None:
        refined, _ = self.reclassify()
        self.assertEqual(refined["POTPOURRI"], OTHER)

    def test_literary_answers_with_operatic_clues_stay_other(self) -> None:
        # The answers alone say Literature; the clue words say Music.
        shares = mean_subject_shares(
            RUSSIAN_WORKS, build_answer_votes(self.category_answers, self.taxonomy)
        )
        self.assertEqual(shares["Literature"], 1.0)
        refined, _ = self.reclassify()
        self.assertEqual(refined["RUSSIAN OPERA"], OTHER)

    def test_word_game_whose_answers_are_body_parts_stays_wordplay(self) -> None:
        refined, _ = self.reclassify()
        self.assertEqual(refined["AN ANATOMY OF WORDS"], WORDPLAY)

    def test_word_game_moves_once_its_clues_are_about_anatomy(self) -> None:
        self.category_clues["AN ANATOMY OF WORDS"] = [ANATOMY_CLUE] * len(BODY_PARTS)
        self.assertEqual(self.moved()["AN ANATOMY OF WORDS"], "Science")

    def test_manual_override_is_never_moved(self) -> None:
        refined, found = self.reclassify(protected={"A NOVEL PASSAGE", 'CAPITAL "A"'})
        self.assertEqual(refined["A NOVEL PASSAGE"], OTHER)
        self.assertEqual(refined['CAPITAL "A"'], LETTER_PUNS)
        moved = {item["category"] for item in found}
        self.assertNotIn("A NOVEL PASSAGE", moved)
        self.assertNotIn('CAPITAL "A"', moved)

    def test_a_labelled_category_whose_cards_point_elsewhere_moves(self) -> None:
        refined, found = self.reclassify()
        self.assertEqual(refined["BOOK CLUB"], moved_to("Literature"))
        item = next(i for i in found if i["category"] == "BOOK CLUB")
        self.assertEqual(item["source_subject"], "Film & TV")

    def test_correctly_labelled_categories_stay_as_they_are(self) -> None:
        refined, found = self.reclassify()
        moved = {item["category"] for item in found}
        for category in ("NOVELS", "MOVIES", "WORLD CAPITALS", "THE BODY HUMAN"):
            with self.subTest(category=category):
                self.assertEqual(refined[category], self.taxonomy[category])
                self.assertNotIn(category, moved)

    def test_a_label_the_evidence_confirms_is_left_untouched(self) -> None:
        # Three books categories share every answer, so each one's evidence is
        # decisive for Literature: its own label. That is not a move, and its
        # specific sub-category must survive.
        for name in ("BRONTES", "YORKSHIRE NOVELS", "GOTHIC CLASSICS"):
            self.taxonomy[name] = LITERATURE
            self.category_answers[name] = ["jane eyre", "villette", "shirley"]
            self.category_clues[name] = [LITERARY_CLUE] * 3
        refined, found = self.reclassify()
        self.assertEqual(refined["BRONTES"], LITERATURE)
        self.assertNotIn("BRONTES", {item["category"] for item in found})

    def test_a_category_cannot_confirm_its_own_label(self) -> None:
        # Each answer is in two books categories, one other films category, and
        # the films category being judged. Counting its own votes would split
        # them 2-2; without them the books share is 2/3.
        answers = ["emma", "persuasion", "sanditon"]
        for name, entry, clue in (
            ("AUSTEN", LITERATURE, LITERARY_CLUE),
            ("REGENCY NOVELS", LITERATURE, LITERARY_CLUE),
            ("PERIOD DRAMAS", FILM, FILM_CLUE),
            ("PAGE TO SCREEN", FILM, LITERARY_CLUE),
        ):
            self.taxonomy[name] = entry
            self.category_answers[name] = answers
            self.category_clues[name] = [clue] * len(answers)
        self.assertEqual(self.moved()["PAGE TO SCREEN"], "Literature")

    def test_category_missing_from_the_taxonomy_is_a_candidate(self) -> None:
        del self.taxonomy['CAPITAL "C\\"']
        refined, found = self.reclassify()
        self.assertEqual(refined['CAPITAL "C\\"'], moved_to("Geography"))
        item = next(i for i in found if i["category"] == 'CAPITAL "C\\"')
        self.assertEqual(item["source_subject"], SUBJECT_OTHER)

    def test_category_with_too_few_cards_is_left_alone(self) -> None:
        for cards in (1, 2):
            with self.subTest(cards=cards):
                self.category_answers["A NOVEL PASSAGE"] = NOVELS[:cards]
                self.assertNotIn("A NOVEL PASSAGE", self.moved())

    def test_a_three_card_category_is_judged(self) -> None:
        self.category_answers["A NOVEL PASSAGE"] = NOVELS[:3]
        self.assertEqual(self.moved()["A NOVEL PASSAGE"], "Literature")

    def test_clues_without_words_cannot_confirm_a_move(self) -> None:
        self.category_clues["A NOVEL PASSAGE"] = ["<i>1984</i>"] * len(NOVELS)
        self.assertNotIn("A NOVEL PASSAGE", self.moved())

    def test_specific_subcategory_from_the_name_only_pass_is_replaced(self) -> None:
        self.taxonomy["A NOVEL PASSAGE"] = ("Other", "Card Games", "")
        refined, _ = self.reclassify()
        self.assertEqual(refined["A NOVEL PASSAGE"], moved_to("Literature"))

    def test_secondary_subject_duplicating_the_new_subject_is_cleared(self) -> None:
        self.taxonomy['CAPITAL "A"'] = (
            "Wordplay & Language",
            "Letter Puns",
            "Geography",
        )
        refined, _ = self.reclassify()
        self.assertEqual(refined['CAPITAL "A"'], moved_to("Geography"))

    def test_other_secondary_subject_is_kept(self) -> None:
        self.taxonomy['CAPITAL "A"'] = ("Wordplay & Language", "Letter Puns", "History")
        refined, _ = self.reclassify()
        self.assertEqual(refined['CAPITAL "A"'], moved_to("Geography", "History"))

    def test_input_is_not_mutated_and_result_is_deterministic(self) -> None:
        before = dict(self.taxonomy)
        first, second = self.reclassify(), self.reclassify()
        self.assertEqual(self.taxonomy, before)
        self.assertEqual(first, second)

    def test_results_are_largest_first_then_alphabetical(self) -> None:
        for name, answers in (
            ("A NOVEL BIGGER", NOVELS * 2),
            ("A NOVEL AGAIN", NOVELS),
        ):
            self.taxonomy[name] = OTHER
            self.category_answers[name] = answers
            self.category_clues[name] = [LITERARY_CLUE] * len(answers)
        _, found = self.reclassify()
        literary = [i["category"] for i in found if i["subject"] == "Literature"]
        self.assertEqual(
            literary,
            ["A NOVEL BIGGER", "A NOVEL AGAIN", "A NOVEL PASSAGE", "BOOK CLUB"],
        )

    def test_report_names_the_runner_up(self) -> None:
        _, found = self.reclassify()
        item = next(i for i in found if i["category"] == "A NOVEL PASSAGE")
        self.assertEqual(item["runner_up"], "Film & TV")
        self.assertAlmostEqual(item["mean_share"], 0.9)
        self.assertAlmostEqual(item["runner_up_share"], 0.1)


class AnswerVoteTests(unittest.TestCase):
    """How answers accumulate evidence from the rest of the deck."""

    def setUp(self) -> None:
        self.taxonomy: dict[str, TaxonomyEntry] = {
            "NOVELS": LITERATURE,
            "CLASSIC LIT": LITERATURE,
            "MOVIES": FILM,
            "RHYME TIME": WORDPLAY,
            "GRAB BAG": OTHER,
        }

    def test_a_category_votes_once_per_answer_however_often_it_repeats(self) -> None:
        answers = {"NOVELS": ["emma", "emma", "emma"], "CLASSIC LIT": ["emma"]}
        votes = build_answer_votes(answers, self.taxonomy)
        self.assertEqual(dict(votes["emma"]), {"Literature": 2})

    def test_wordplay_other_and_unclassified_categories_do_not_vote(self) -> None:
        answers = {
            "NOVELS": ["emma"],
            "RHYME TIME": ["emma"],
            "GRAB BAG": ["emma"],
            "NEVER SEEN": ["emma"],
        }
        votes = build_answer_votes(answers, self.taxonomy)
        self.assertEqual(dict(votes["emma"]), {"Literature": 1})

    def test_wordplay_votes_do_not_dilute_an_answer(self) -> None:
        answers = {
            "NOVELS": ["emma"],
            "CLASSIC LIT": ["emma"],
            "RHYME TIME": ["emma"] * 5,
        }
        votes = build_answer_votes(answers, self.taxonomy)
        self.assertEqual(answer_shares(votes["emma"]), {"Literature": 1.0})

    def test_split_votes_give_fractional_shares(self) -> None:
        self.assertEqual(
            answer_shares({"Literature": 21, "Film & TV": 21}),
            {"Literature": 0.5, "Film & TV": 0.5},
        )

    def test_a_single_vote_is_an_anecdote_not_evidence(self) -> None:
        self.assertEqual(answer_shares({"Literature": 1}), {})

    def test_the_judged_category_s_own_vote_is_taken_out(self) -> None:
        self.assertEqual(
            answer_shares({"Literature": 2, "Film & TV": 1}, "Film & TV"),
            {"Literature": 1.0},
        )

    def test_without_its_own_vote_an_answer_may_lack_evidence(self) -> None:
        self.assertEqual(answer_shares({"Film & TV": 2}, "Film & TV"), {})

    def test_an_own_subject_without_votes_changes_nothing(self) -> None:
        self.assertEqual(
            answer_shares({"Literature": 2}, "Film & TV"), {"Literature": 1.0}
        )

    def test_an_unknown_answer_has_no_evidence(self) -> None:
        self.assertEqual(answer_shares({}), {})

    def test_unknown_answers_count_against_every_subject(self) -> None:
        votes = {"emma": {"Literature": 2}}
        self.assertEqual(
            mean_subject_shares(["emma", "new"], votes), {"Literature": 0.5}
        )

    def test_an_empty_category_has_no_shares(self) -> None:
        self.assertEqual(mean_subject_shares([], {}), {})


class DecisionTests(unittest.TestCase):
    """Winner-take-all: a bar that depends on the source label, and a margin."""

    def decide(self, shares: dict[str, float], source: str) -> bool:
        return is_decisive(rank_subjects(shares), source)

    def test_winner_and_runner_up_are_the_top_two_subjects(self) -> None:
        evidence = rank_subjects({"Music": 0.2, "Geography": 0.6, "History": 0.1})
        self.assertEqual(evidence, SubjectEvidence("Geography", 0.6, "Music", 0.2))

    def test_runner_up_can_have_no_share(self) -> None:
        evidence = rank_subjects({"Science": 0.5})
        self.assertEqual(evidence.subject, "Science")
        self.assertEqual(evidence.runner_up_share, 0.0)

    def test_ties_keep_the_order_of_subjects(self) -> None:
        evidence = rank_subjects({"Geography": 0.4, "History": 0.4})
        self.assertEqual(
            (evidence.subject, evidence.runner_up), ("History", "Geography")
        )

    def test_wordplay_and_other_are_never_winners(self) -> None:
        evidence = rank_subjects(
            {SUBJECT_WORDPLAY: 0.9, SUBJECT_OTHER: 0.9, "Art": 0.05}
        )
        self.assertEqual(evidence.subject, "Art")

    def test_other_needs_the_other_bar(self) -> None:
        bar = EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE[SUBJECT_OTHER]
        self.assertTrue(self.decide({"Geography": bar}, SUBJECT_OTHER))
        self.assertFalse(self.decide({"Geography": bar - 0.01}, SUBJECT_OTHER))

    def test_wordplay_needs_a_higher_bar_than_other(self) -> None:
        wordplay_bar = EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE[SUBJECT_WORDPLAY]
        other_bar = EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE[SUBJECT_OTHER]
        self.assertGreater(wordplay_bar, other_bar)
        between = {"Geography": (wordplay_bar + other_bar) / 2}
        self.assertTrue(self.decide(between, SUBJECT_OTHER))
        self.assertFalse(self.decide(between, SUBJECT_WORDPLAY))
        self.assertTrue(self.decide({"Geography": wordplay_bar}, SUBJECT_WORDPLAY))

    def test_a_real_subject_needs_the_labelled_bar(self) -> None:
        other_bar = EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE[SUBJECT_OTHER]
        self.assertGreater(EVIDENCE_MIN_MEAN_SHARE_LABELED, other_bar)
        for source in ("Film & TV", "People", "Pop Culture", "History"):
            with self.subTest(source=source):
                self.assertEqual(
                    EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE[source],
                    EVIDENCE_MIN_MEAN_SHARE_LABELED,
                )
        between = {"Geography": (EVIDENCE_MIN_MEAN_SHARE_LABELED + other_bar) / 2}
        self.assertTrue(self.decide(between, SUBJECT_OTHER))
        self.assertFalse(self.decide(between, "Film & TV"))
        labelled = {"Geography": EVIDENCE_MIN_MEAN_SHARE_LABELED}
        self.assertTrue(self.decide(labelled, "Film & TV"))

    def test_a_close_runner_up_blocks_the_move(self) -> None:
        close = {"History": 0.55, "Geography": 0.55 - EVIDENCE_MIN_MARGIN + 0.05}
        self.assertFalse(self.decide(close, SUBJECT_OTHER))

    def test_a_margin_of_exactly_the_minimum_is_enough(self) -> None:
        # 3/5 vs 2/5: 0.6 - 0.4 is 0.19999999999999996 in floating point.
        self.assertTrue(
            self.decide({"History": 3 / 5, "Geography": 2 / 5}, SUBJECT_OTHER)
        )

    def test_a_share_of_exactly_the_bar_is_enough(self) -> None:
        # 9/20 is 0.45 (the "Other" bar), reached by summing twenty 0/1 shares.
        shares = mean_subject_shares(
            ["lit"] * 9 + ["new"] * 11, {"lit": {"Literature": 2}}
        )
        self.assertTrue(self.decide(shares, SUBJECT_OTHER))

    def test_rounding_just_under_the_bar_still_counts(self) -> None:
        # Literary shares of 2/10 and 7/10 average to exactly 0.45, which
        # floating point computes as 0.44999999999999996.
        bar = EVIDENCE_MIN_MEAN_SHARE_BY_SOURCE[SUBJECT_OTHER]
        votes = {
            "a": {"Literature": 2, "History": 2, "Geography": 2, "Art": 2, "Music": 2},
            "b": {"Literature": 7, "Sports": 1, "Science": 1, "Food & Drink": 1},
        }
        shares = mean_subject_shares(["a", "b"], votes)
        self.assertAlmostEqual(shares["Literature"], bar)  # fixture assumes 0.45
        self.assertLess(shares["Literature"], bar)
        self.assertTrue(self.decide(shares, SUBJECT_OTHER))

    def test_every_known_label_is_re_examined_unless_pinned(self) -> None:
        taxonomy: dict[str, TaxonomyEntry] = {
            "A": OTHER,
            "B": WORDPLAY,
            "C": FILM,
            "D": OTHER,
            "E": ("Astrology", "Signs", ""),
        }
        self.assertEqual(source_subject("A", taxonomy, ()), SUBJECT_OTHER)
        self.assertEqual(source_subject("B", taxonomy, ()), SUBJECT_WORDPLAY)
        self.assertEqual(source_subject("C", taxonomy, ()), "Film & TV")
        self.assertEqual(source_subject("MISSING", taxonomy, ()), SUBJECT_OTHER)
        self.assertIsNone(source_subject("D", taxonomy, {"D"}))
        self.assertIsNone(source_subject("E", taxonomy, ()))


class ClueVocabularyTests(unittest.TestCase):
    """The second signal: which subject's clue words these clues resemble."""

    def setUp(self) -> None:
        self.taxonomy: dict[str, TaxonomyEntry] = {
            "NOVELS": LITERATURE,
            "OPERA": MUSIC,
            "DOUBLE MEANINGS": WORDPLAY,
            "GRAB BAG": OTHER,
        }
        self.clues: dict[str, list[str]] = {
            "NOVELS": [LITERARY_CLUE] * 3,
            "OPERA": [OPERA_CLUE] * 3,
            "DOUBLE MEANINGS": [WORD_CLUE] * 3,
            "GRAB BAG": ["opera opera opera opera"] * 50,
            "NEVER CLASSIFIED": ["opera opera opera"] * 50,
        }
        self.vocabulary = build_clue_vocabulary(self.clues, self.taxonomy)

    def test_words_are_casefolded_without_markup_or_one_letter_words(self) -> None:
        self.assertEqual(
            clue_words('<i>Sung</i> in "Aida\\" [sound:x.mp3] O! Verdi\'s'),
            ["sung", "in", "aida", "verdi's"],
        )

    def test_other_and_unclassified_categories_teach_nothing(self) -> None:
        self.assertEqual(set(self.vocabulary.word_counts), set(EVIDENCE_CLUE_SUBJECTS))
        self.assertEqual(self.vocabulary.word_counts["Music"]["opera"], 3)

    def test_clues_are_matched_to_the_subject_that_uses_their_words(self) -> None:
        self.assertEqual(
            clue_subject(["An aria from the opera"], self.vocabulary), "Music"
        )
        self.assertEqual(
            clue_subject(["The novel's author"], self.vocabulary), "Literature"
        )
        self.assertEqual(
            clue_subject(["Another meaning of this word"], self.vocabulary),
            SUBJECT_WORDPLAY,
        )

    def test_a_subject_does_not_win_by_size(self) -> None:
        self.clues["BIG BOOKS"] = ["the novel by the author was a book"] * 500
        self.taxonomy["BIG BOOKS"] = LITERATURE
        vocabulary = build_clue_vocabulary(self.clues, self.taxonomy)
        self.assertEqual(clue_subject(["An opera aria"], vocabulary), "Music")

    def test_a_smaller_subject_whose_words_fit_better_wins(self) -> None:
        # "aria" is 50% of the small subject's words but 25% of the big one's.
        vocabulary = build_clue_vocabulary(
            {
                "TINY": ["opera aria"],
                "HUGE": ["opera aria"] * 1000 + ["opera tenor"] * 1000,
            },
            {"TINY": LITERATURE, "HUGE": MUSIC},
        )
        self.assertEqual(clue_subject(["aria"], vocabulary), "Literature")

    def test_each_subject_word_probabilities_sum_to_one(self) -> None:
        words = {w for counts in self.vocabulary.word_counts.values() for w in counts}
        for subject, total in self.vocabulary.totals.items():
            if not total:
                continue
            with self.subTest(subject=subject):
                probability = sum(
                    math.exp(clue_log_likelihood({word: 1}, subject, self.vocabulary))
                    for word in words
                )
                self.assertAlmostEqual(probability, 1.0)

    def test_clues_without_words_name_no_subject(self) -> None:
        self.assertIsNone(clue_subject(["<b>1984</b>", "?"], self.vocabulary))
        self.assertIsNone(clue_subject([], self.vocabulary))

    def test_subjects_without_clue_words_are_never_chosen(self) -> None:
        # Smoothing gives an empty subject the same probability for any word,
        # which would otherwise beat the real subjects on unfamiliar clues.
        subject = clue_subject(["Zebras quietly juggle xylophones"], self.vocabulary)
        self.assertIn(subject, {"Literature", "Music", SUBJECT_WORDPLAY})

    def test_no_subject_is_named_before_any_clues_are_learned(self) -> None:
        empty = build_clue_vocabulary({}, {})
        self.assertIsNone(clue_subject(["An aria from the opera"], empty))


if __name__ == "__main__":
    unittest.main()
