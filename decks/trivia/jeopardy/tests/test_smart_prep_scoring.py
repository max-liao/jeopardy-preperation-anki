"""score_notes(): sub-categories that mean "no topic" earn no sub-category credit.

"Miscellaneous" is the loader's default for a category missing from the taxonomy,
and "Unclassified" is the label consolidate_taxonomy.py gives every catch-all.
Both mark the absence of a topic, so a card filed under either is scored as if its
sub-category never recurs, however many cards share the label.
"""

import unittest

from decks.trivia.jeopardy.smart_prep import NoteMeta, score_notes

YEAR = 2024
ANSWER = "cairo"
SUBJECT = "Geography"
# Every label is equally common, so only the no-topic rule can tell them apart.
LABEL_FREQUENCY = 50.0
# The labels as they appear in category_taxonomy.json and the loader's default.
UNCLASSIFIED = "Unclassified"
MISCELLANEOUS = "Miscellaneous"


def note(subcat_label: str) -> NoteMeta:
    """A note that differs from the others only in its sub-category label."""
    return (ANSWER, subcat_label.casefold(), SUBJECT, subcat_label, "", YEAR, 1.0)


def scores_by_label(labels: list[str]) -> dict[str, int]:
    """Score one note per label; return label -> 0-100 score."""
    meta = {note_id: note(label) for note_id, label in enumerate(labels)}
    scored = score_notes(
        meta,
        answer_score={ANSWER: 1.0},
        subcat_score={label.casefold(): LABEL_FREQUENCY for label in labels},
        subject_score={SUBJECT: 1.0},
        secondary_subject_score={},
        answer_last_seen={ANSWER: YEAR},
    )
    return {labels[note_id]: score for note_id, (score, _tier) in scored.items()}


class NoTopicSubcategoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scores = scores_by_label(
            ["Capitals", "Rivers", UNCLASSIFIED, MISCELLANEOUS]
        )

    def test_unclassified_earns_no_subcategory_credit(self) -> None:
        self.assertLess(self.scores[UNCLASSIFIED], self.scores["Capitals"])

    def test_miscellaneous_still_earns_no_subcategory_credit(self) -> None:
        self.assertLess(self.scores[MISCELLANEOUS], self.scores["Capitals"])

    def test_both_no_topic_labels_score_alike(self) -> None:
        self.assertEqual(self.scores[UNCLASSIFIED], self.scores[MISCELLANEOUS])

    def test_real_subcategories_keep_their_credit(self) -> None:
        self.assertEqual(self.scores["Capitals"], self.scores["Rivers"])
        self.assertGreater(self.scores["Rivers"], self.scores[UNCLASSIFIED])

    def test_a_label_that_only_contains_the_word_is_a_real_topic(self) -> None:
        scores = scores_by_label(["Capitals", "Unclassified Documents"])
        self.assertEqual(scores["Capitals"], scores["Unclassified Documents"])


if __name__ == "__main__":
    unittest.main()
