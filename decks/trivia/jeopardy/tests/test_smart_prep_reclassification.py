"""End-to-end: a refresh re-homes misplaced categories and leaves study data untouched.

Runs the real `compute_scores` and `apply_scores_and_tags` against an in-memory,
Anki-shaped database, then checks the back-of-card details field and that the
review log, cards and note content are byte-for-byte unchanged.
"""

import csv
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from decks.trivia.jeopardy.consolidate_taxonomy import MANUAL_OVERRIDES
from decks.trivia.jeopardy.tests.db_fixtures import (
    FIELD_SEPARATOR,
    add_reviewed_jeopardy_note,
    create_collection_schema,
    jeopardy_fields,
)
from decks.trivia.jeopardy.jeopardy_consts import (
    CARD_REPORT_COLUMNS,
    CARD_SUBJECT_MIN_GAP_BY_SOURCE,
    EVIDENCE_REPORT_COLUMNS,
    JEOPARDY_NOTETYPE_ID,
    THEME_WINDOW_YEARS,
    TOTAL_FIELDS,
)
from decks.trivia.jeopardy.jeopardy_types import CardText
from decks.trivia.jeopardy.smart_prep import (
    apply_scores_and_tags,
    build_recent_subject_counts,
    compute_scores,
    read_cards,
)
from decks.trivia.jeopardy.verify_refresh import differences, take_fingerprint

SCORE_ORD = TOTAL_FIELDS  # the appended "Frequency Score" field
DETAILS_ORD = TOTAL_FIELDS + 1  # the appended "Frequency Details" field
LATEST_YEAR = 2024

# Real answers of the "A NOVEL PASSAGE" category (each clue quotes the novel).
NOVEL_PASSAGE = [
    "The Three Musketeers",
    "Gulliver's Travels",
    "One Flew Over the Cuckoo's Nest",
    "Wuthering Heights",
    "The Stranger",
]
FILMS = ["Casablanca", "Citizen Kane", "Rocky", "One Flew Over the Cuckoo's Nest"]
# The real 'CAPITAL "C"' (its name carries the TSV's stray backslash) and
# 'CAPITAL "A"' categories.
C_CAPITALS = ["Copenhagen", "Cape Town", "Cairo", "Caracas", "Canberra"]
A_CAPITALS = ["Andorra", "Annapolis", "Albany", "Adelaide", "Accra"]
RUSSIAN_WORKS = ["War and Peace", "Lady Macbeth", "Eugene Onegin", "Boris Godunov"]
GRAB_BAG = ["Casablanca", "Copenhagen", "Wuthering Heights", "Rocky", "Cairo"]

LITERARY_CLUE = "This novel by the author has a title character in the book"
FILM_CLUE = "This film starred the actor & the movie director won an Oscar"
CAPITAL_CLUE = "This capital city of the country lies on a river"
OPERA_CLUE = "This opera by the composer has an aria sung by the tenor"
WORD_CLUE = "This word also has a meaning as a term for something else"
DESSERT_CLUE = "This sweet dessert is served after the meal with cream & sugar"
PEAK_CLUE = "This mountain peak rises above the snow line in the range"
ISLAND_CLUE = "This island lies in the sea off the coast of the mainland"
DESSERTS = ["tiramisu", "baklava", "flan", "cheesecake"]

# A category a human pinned as Other in MANUAL_OVERRIDES. Its cards are all
# literary, so only the pin keeps it out of Literature.
PINNED_CATEGORY = "MISCELLANEOUS"
C_CATEGORY = 'CAPITAL "C\\"'
A_CATEGORY = 'CAPITAL "A"'
# A wordplay category whose answers are only ever asked in other wordplay
# categories, so it has no evidence and stays put with its secondary subject.
RHYME_CATEGORY = "SCIENCE RHYME TIME"
# The name-only pass filed this pun under Food & Drink; every clue is a film.
FILM_PUN_CATEGORY = "SNICKERS"
# A pun the name-only pass could not place. Three of its four answers are asked in
# two Geography categories each, so the category rule moves it to Geography.
HIGH_POINTS = "HIGH POINTS"

# A grab-bag whose cards each test something different, so no category rule can
# place it. Each card's answer and clue are (answer, clue, the subject it shows);
# None means the evidence cannot place it and it keeps showing "Other".
MIXED_CATEGORY = "ANYTHING GOES"
# An image-only clue has no words, so only the answer's votes can place its card.
# "Zanzibar Peak" is asked in one Geography category and in HIGH POINTS, which the
# category rule moves to Geography first: the card is placed only because the
# moved category votes.
IMAGE_CLUE = "<img src='clue.jpg'>"
MIXED_CARDS: list[tuple[str, str, str | None]] = [
    ("Wuthering Heights", LITERARY_CLUE, "Literature"),
    ("Casablanca", FILM_CLUE, "Film & TV"),
    ("Cairo", CAPITAL_CLUE, "Geography"),
    ("bat", WORD_CLUE, None),
    ("Wuthering Heights", IMAGE_CLUE, "Literature"),
    ("Zanzibar Peak", IMAGE_CLUE, "Geography"),
    ("nothing at all", IMAGE_CLUE, None),
]

# category -> (subject and sub-category the name-only LLM pass gave it, answers,
# the clue on each of its cards). The puns and grab-bags are "Other"; the letter
# game the LLM could read is "Wordplay & Language".
DECK: dict[str, tuple[tuple[str, str], list[str], str]] = {
    "NOVELS": (("Literature", "Novels"), NOVEL_PASSAGE, LITERARY_CLUE),
    "CLASSIC LIT": (("Literature", "Novels"), NOVEL_PASSAGE, LITERARY_CLUE),
    "RUSSIAN NOVELS": (("Literature", "Novels"), RUSSIAN_WORKS, LITERARY_CLUE),
    "RUSSIAN AUTHORS": (("Literature", "Authors"), RUSSIAN_WORKS, LITERARY_CLUE),
    "MOVIES": (("Film & TV", "Movies"), FILMS, FILM_CLUE),
    "CINEMA CLASSICS": (("Film & TV", "Movies"), FILMS, FILM_CLUE),
    "WORLD CAPITALS": (
        ("Geography", "Capitals"),
        C_CAPITALS + A_CAPITALS,
        CAPITAL_CLUE,
    ),
    "CAPITAL CITIES": (
        ("Geography", "Capitals"),
        C_CAPITALS + A_CAPITALS,
        CAPITAL_CLUE,
    ),
    "OPERA": (("Music", "Opera"), ["La Boheme", "Carmen", "Aida", "Tosca"], OPERA_CLUE),
    "COMPOSERS": (("Music", "Composers"), ["Verdi", "Puccini", "Bizet"], OPERA_CLUE),
    "DOUBLE MEANINGS": (
        ("Wordplay & Language", "Wordplay"),
        ["bat", "bark"],
        WORD_CLUE,
    ),
    "VOCABULARY": (("Wordplay & Language", "Vocabulary"), ["ephemeral"] * 5, WORD_CLUE),
    "A NOVEL PASSAGE": (("Other", "Unclassified"), NOVEL_PASSAGE, LITERARY_CLUE),
    C_CATEGORY: (("Other", "Unclassified"), C_CAPITALS, CAPITAL_CLUE),
    A_CATEGORY: (("Wordplay & Language", "Letter Puns"), A_CAPITALS, CAPITAL_CLUE),
    "RUSSIAN OPERA": (("Other", "Unclassified"), RUSSIAN_WORKS, OPERA_CLUE),
    "A LITTLE OF EVERYTHING": (("Other", "Unclassified"), GRAB_BAG, WORD_CLUE),
    PINNED_CATEGORY: (("Other", "Unclassified"), NOVEL_PASSAGE, LITERARY_CLUE),
    RHYME_CATEGORY: (
        ("Wordplay & Language", "Rhyme Time"),
        ["bat", "bark", "ephemeral"],
        WORD_CLUE,
    ),
    "DESSERTS": (("Food & Drink", "Desserts"), DESSERTS, DESSERT_CLUE),
    "SWEET ENDINGS": (("Food & Drink", "Desserts"), DESSERTS, DESSERT_CLUE),
    FILM_PUN_CATEGORY: (("Food & Drink", "Candy"), FILMS, FILM_CLUE),
    "PEAKS": (("Geography", "Mountains"), ["Everest", "K2", "Denali"], PEAK_CLUE),
    "MOUNTAIN RANGES": (
        ("Geography", "Mountains"),
        ["Everest", "K2", "Denali"],
        PEAK_CLUE,
    ),
    "ISLANDS": (("Geography", "Islands"), ["Zanzibar Peak", "Crete"], ISLAND_CLUE),
    HIGH_POINTS: (
        ("Other", "Unclassified"),
        ["Everest", "K2", "Denali", "Zanzibar Peak"],
        PEAK_CLUE,
    ),
}
LABELS: dict[str, tuple[str, str]] = {
    category: label for category, (label, _answers, _clue) in DECK.items()
}
LABELS[MIXED_CATEGORY] = ("Other", "Unclassified")
# The secondary_subject the name-only pass gave two of the wordplay categories.
SECONDARY = {RHYME_CATEGORY: "Science", A_CATEGORY: "Geography"}
TAXONOMY_JSON = {
    category: {
        "subject": subject,
        "sub_category": sub_category,
        "secondary_subject": SECONDARY.get(category, ""),
    }
    for category, (subject, sub_category) in LABELS.items()
}
TAG_RE = re.compile(r"<[^>]+>")


class RefreshReclassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = Path(tmp.name)
        self.taxonomy_path = self.tmp_dir / "taxonomy.json"
        self.taxonomy_path.write_text(json.dumps(TAXONOMY_JSON), encoding="utf-8")

        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        create_collection_schema(self.conn)
        self.note_ids: dict[str, list[int]] = {}
        note_id = 1
        for category, (_label, answers, clue) in DECK.items():
            for answer in answers:
                add_reviewed_jeopardy_note(
                    self.conn,
                    note_id,
                    category,
                    answer,
                    air_date=f"{LATEST_YEAR}-03-16",
                    clue=clue,
                )
                self.note_ids.setdefault(category, []).append(note_id)
                note_id += 1
        self.mixed_subjects: dict[int, str | None] = {}
        for answer, clue, shows in MIXED_CARDS:
            add_reviewed_jeopardy_note(
                self.conn,
                note_id,
                MIXED_CATEGORY,
                answer,
                air_date=f"{LATEST_YEAR}-03-16",
                clue=clue,
            )
            self.note_ids.setdefault(MIXED_CATEGORY, []).append(note_id)
            self.mixed_subjects[note_id] = shows
            note_id += 1
        self.conn.commit()

    def refresh(
        self, evidence_report: Path | None = None, card_report: Path | None = None
    ) -> None:
        """Run the same steps as `smart_prep.py --live-db`, minus the notetype surgery."""
        meta, scored, _subject, _secondary, card_subjects = compute_scores(
            self.conn, self.taxonomy_path, evidence_report, card_report
        )
        latest_year = max(item[5] for item in meta.values())
        counts, start_year = build_recent_subject_counts(
            meta, latest_year, card_subjects
        )
        apply_scores_and_tags(
            self.conn,
            meta,
            scored,
            SCORE_ORD,
            DETAILS_ORD,
            latest_year,
            counts,
            start_year,
            card_subjects,
        )

    def field(self, note_id: int, ordinal: int) -> str:
        (flds,) = self.conn.execute(
            "SELECT flds FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return str(flds.split(FIELD_SEPARATOR)[ordinal])

    def tags(self, note_id: int) -> list[str]:
        (tags,) = self.conn.execute(
            "SELECT tags FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return str(tags).split()

    def details_text(self, note_id: int) -> str:
        return TAG_RE.sub("", self.field(note_id, DETAILS_ORD))

    def assert_theme(self, category: str, subject: str, tag_value: str) -> None:
        for note_id in self.note_ids[category]:
            with self.subTest(category=category, note_id=note_id):
                self.assertIn(
                    f"Frequently asked theme: {subject}", self.details_text(note_id)
                )
                self.assertIn(f"subject:{tag_value}", self.tags(note_id))
                self.assertIn(f"subcat:{tag_value}", self.tags(note_id))

    def test_a_novel_passage_shows_literature_on_the_back_of_the_card(self) -> None:
        self.refresh()
        window = f"{LATEST_YEAR - THEME_WINDOW_YEARS + 1}-{LATEST_YEAR}"
        self.assert_theme("A NOVEL PASSAGE", "Literature", "Literature")
        for note_id in self.note_ids["A NOVEL PASSAGE"]:
            with self.subTest(note_id=note_id):
                self.assertIn(f"times from {window}", self.details_text(note_id))

    def test_capital_c_shows_geography(self) -> None:
        self.refresh()
        self.assert_theme(C_CATEGORY, "Geography", "Geography")

    def test_letter_game_labelled_wordplay_shows_geography(self) -> None:
        self.refresh()
        self.assert_theme(A_CATEGORY, "Geography", "Geography")

    def test_pun_filed_under_the_wrong_subject_shows_its_content(self) -> None:
        self.refresh()
        self.assert_theme(FILM_PUN_CATEGORY, "Film & TV", "Film_TV")

    def test_correctly_labelled_categories_keep_their_labels(self) -> None:
        self.refresh()
        for category, subject, tags in (
            ("DESSERTS", "Food & Drink", ("subject:Food_Drink", "subcat:Desserts")),
            ("MOVIES", "Film & TV", ("subject:Film_TV", "subcat:Movies")),
        ):
            for note_id in self.note_ids[category]:
                with self.subTest(category=category, note_id=note_id):
                    self.assertIn(
                        f"Frequently asked theme: {subject}",
                        self.details_text(note_id),
                    )
                    for tag in tags:
                        self.assertIn(tag, self.tags(note_id))

    def test_grab_bag_of_word_clues_keeps_showing_other(self) -> None:
        self.refresh()
        for note_id in self.note_ids["A LITTLE OF EVERYTHING"]:
            with self.subTest(note_id=note_id):
                self.assertIn(
                    "Frequently asked theme: Other", self.details_text(note_id)
                )
                self.assertIn("subject:Other", self.tags(note_id))

    def test_opera_about_novels_stays_put_but_each_card_shows_music(self) -> None:
        # The answers are novels, so the category rule cannot move the category (it
        # is not in the evidence report). Each clue is about an opera, so each card
        # shows Music, while the category's own sub-category stays.
        report = self.tmp_dir / "moves.tsv"
        self.refresh(evidence_report=report)
        with open(report, encoding="utf-8", newline="") as f:
            moved = {row[0] for row in list(csv.reader(f, delimiter="\t"))[1:]}
        self.assertNotIn("RUSSIAN OPERA", moved)
        for note_id in self.note_ids["RUSSIAN OPERA"]:
            with self.subTest(note_id=note_id):
                self.assertIn(
                    "Frequently asked theme: Music", self.details_text(note_id)
                )
                self.assertIn("subject:Music", self.tags(note_id))
                self.assertIn("subcat:Unclassified", self.tags(note_id))

    def test_manually_pinned_category_keeps_its_label_while_its_cards_show_their_own(
        self,
    ) -> None:
        # The pin keeps the CATEGORY out of Literature (its sub-category tag and
        # its absence from the report prove it was not moved). It does not stop a
        # card's own evidence from showing: these clues are all literary.
        self.assertIn(PINNED_CATEGORY, MANUAL_OVERRIDES)
        report = self.tmp_dir / "moves.tsv"
        self.refresh(evidence_report=report)
        with open(report, encoding="utf-8", newline="") as f:
            moved = {row[0] for row in list(csv.reader(f, delimiter="\t"))[1:]}
        self.assertNotIn(PINNED_CATEGORY, moved)
        for note_id in self.note_ids[PINNED_CATEGORY]:
            with self.subTest(note_id=note_id):
                self.assertIn("subcat:Unclassified", self.tags(note_id))
                self.assertNotIn("subcat:Literature", self.tags(note_id))
                self.assertIn("subject:Literature", self.tags(note_id))

    def test_each_card_of_a_mixed_category_shows_its_own_subject(self) -> None:
        self.refresh()
        for note_id, shows in self.mixed_subjects.items():
            with self.subTest(note_id=note_id, shows=shows):
                theme = "Other" if shows is None else shows
                tag = theme.replace(" & ", "_")
                self.assertIn(
                    f"Frequently asked theme: {theme}", self.details_text(note_id)
                )
                self.assertIn(f"subject:{tag}", self.tags(note_id))
                self.assertIn("subcat:Unclassified", self.tags(note_id))

    def subject_class(self, note_id: int) -> str | None:
        """The badge's subject class code ("s7"...), or None for a bare "Other" badge."""
        match = re.search(r'class="([^"]*)"', self.field(note_id, SCORE_ORD))
        assert match is not None
        codes = [c for c in match.group(1).split() if re.fullmatch(r"s\d+", c)]
        return codes[0] if codes else None

    def test_the_badge_shows_the_cards_own_subject(self) -> None:
        self.refresh()
        reference = {
            "Literature": self.note_ids["NOVELS"][0],
            "Film & TV": self.note_ids["MOVIES"][0],
            "Geography": self.note_ids["WORLD CAPITALS"][0],
        }
        for note_id, shows in self.mixed_subjects.items():
            with self.subTest(note_id=note_id, shows=shows):
                if shows is None:
                    self.assertIsNone(self.subject_class(note_id))
                else:
                    self.assertIsNotNone(self.subject_class(note_id))
                    self.assertEqual(
                        self.subject_class(note_id),
                        self.subject_class(reference[shows]),
                    )

    def test_per_card_subjects_change_what_is_shown_but_not_the_scores(self) -> None:
        shown = compute_scores(self.conn, self.taxonomy_path)
        with mock.patch.dict(CARD_SUBJECT_MIN_GAP_BY_SOURCE, clear=True):
            unlabeled = compute_scores(self.conn, self.taxonomy_path)
        self.assertTrue(shown[4])
        self.assertEqual(unlabeled[4], {})
        for index in range(4):  # meta, scored, subject_score, secondary_subject_score
            with self.subTest(index=index):
                self.assertEqual(shown[index], unlabeled[index])

    def test_a_category_listed_in_no_card_subjects_keeps_showing_other(self) -> None:
        with mock.patch("decks.trivia.jeopardy.smart_prep.NO_CARD_SUBJECTS", frozenset({MIXED_CATEGORY})):
            self.refresh()
        for note_id in self.note_ids[MIXED_CATEGORY]:
            with self.subTest(note_id=note_id):
                self.assertIn(
                    "Frequently asked theme: Other", self.details_text(note_id)
                )
                self.assertIn("subject:Other", self.tags(note_id))
        # The other grab-bags' cards still show their own subjects.
        note_id = self.note_ids["RUSSIAN OPERA"][0]
        self.assertIn("Frequently asked theme: Music", self.details_text(note_id))

    def test_appeared_count_counts_each_card_under_the_subject_it_shows(self) -> None:
        self.refresh()
        totals: dict[str, int] = {}
        for note_id in range(1, sum(len(v) for v in self.note_ids.values()) + 1):
            for tag in self.tags(note_id):
                if tag.startswith("subject:"):
                    totals[tag] = totals.get(tag, 0) + 1
        for tag, subject in (
            ("subject:Literature", "Literature"),
            ("subject:Music", "Music"),
            ("subject:Other", "Other"),
        ):
            note_id = next(
                n
                for n in range(1, sum(len(v) for v in self.note_ids.values()) + 1)
                if tag in self.tags(n)
            )
            with self.subTest(subject=subject):
                self.assertIn(
                    f"Appeared: {totals[tag]:,} times", self.details_text(note_id)
                )

    def test_evidence_report_lists_every_move(self) -> None:
        report = self.tmp_dir / "moves.tsv"
        self.refresh(evidence_report=report)
        with open(report, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f, delimiter="\t"))
        self.assertEqual(tuple(rows[0]), EVIDENCE_REPORT_COLUMNS)
        moves = {row[0]: (row[2], row[3]) for row in rows[1:]}
        self.assertEqual(
            moves,
            {
                "A NOVEL PASSAGE": ("Other", "Literature"),
                C_CATEGORY: ("Other", "Geography"),
                A_CATEGORY: ("Wordplay & Language", "Geography"),
                FILM_PUN_CATEGORY: ("Food & Drink", "Film & TV"),
                HIGH_POINTS: ("Other", "Geography"),
            },
        )

    def test_card_report_lists_every_labeled_card(self) -> None:
        report = self.tmp_dir / "cards.tsv"
        self.refresh(card_report=report)
        with open(report, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f, delimiter="\t"))
        self.assertEqual(tuple(rows[0]), CARD_REPORT_COLUMNS)
        labeled = {int(row[0]): (row[1], row[2], row[3]) for row in rows[1:]}
        for note_id, shows in self.mixed_subjects.items():
            with self.subTest(note_id=note_id):
                if shows is None:
                    self.assertNotIn(note_id, labeled)
                else:
                    self.assertEqual(labeled[note_id], (MIXED_CATEGORY, "Other", shows))
        self.assertEqual({source for _c, source, _to in labeled.values()}, {"Other"})
        minimum = CARD_SUBJECT_MIN_GAP_BY_SOURCE["Other"]
        for row in rows[1:]:
            with self.subTest(note_id=row[0]):
                self.assertGreaterEqual(float(row[4]), minimum - 0.01)  # rounded to 2dp

    def test_secondary_subject_tag_is_written_once_across_refreshes(self) -> None:
        self.refresh()
        self.refresh()
        for note_id in self.note_ids[RHYME_CATEGORY]:
            with self.subTest(note_id=note_id):
                self.assertEqual(self.tags(note_id).count("subcat2:Science"), 1)

    def test_stale_secondary_subject_tags_are_dropped(self) -> None:
        # Earlier refreshes left duplicated subcat2 tags. The move to Geography
        # clears the category's now-redundant secondary subject, so none remain.
        for note_id in self.note_ids[A_CATEGORY]:
            self.conn.execute(
                "UPDATE notes SET tags = ? WHERE id = ?",
                (" leech subcat2:Geography subcat2:Geography ", note_id),
            )
        self.refresh()
        for note_id in self.note_ids[A_CATEGORY]:
            with self.subTest(note_id=note_id):
                tags = self.tags(note_id)
                self.assertEqual([t for t in tags if t.startswith("subcat2:")], [])
                self.assertIn("leech", tags)

    def test_refresh_leaves_study_data_untouched(self) -> None:
        before = take_fingerprint(self.conn)
        (reviews_before,) = self.conn.execute("SELECT COUNT(*) FROM revlog").fetchone()
        self.refresh()
        (reviews_after,) = self.conn.execute("SELECT COUNT(*) FROM revlog").fetchone()
        self.assertEqual(reviews_before, reviews_after)
        self.assertEqual(differences(before, take_fingerprint(self.conn)), [])

    def test_refresh_is_idempotent(self) -> None:
        self.refresh()
        first = self.conn.execute(
            "SELECT id, flds, tags FROM notes ORDER BY id"
        ).fetchall()
        self.refresh()
        second = self.conn.execute(
            "SELECT id, flds, tags FROM notes ORDER BY id"
        ).fetchall()
        self.assertEqual(first, second)


class ReadCardsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        create_collection_schema(self.conn)

    def insert(
        self, note_id: int, flds: str, notetype: int = JEOPARDY_NOTETYPE_ID
    ) -> None:
        self.conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, 1, 0, '', ?, ?, 0, 0, '')",
            (note_id, f"guid{note_id}", notetype, flds, str(note_id)),
        )

    def test_a_card_carries_its_normalized_category_clue_and_answer_key(self) -> None:
        self.insert(
            5,
            jeopardy_fields("  novels ", "The Stranger", clue="<b>Bold</b> clue"),
        )
        self.assertEqual(
            read_cards(self.conn),
            [CardText(5, "NOVELS", "<b>Bold</b> clue", "stranger")],
        )

    def test_an_unusable_answer_gives_an_empty_key_but_the_card_is_kept(self) -> None:
        self.insert(6, jeopardy_fields("NOVELS", "...", clue="a clue"))
        self.assertEqual(read_cards(self.conn), [CardText(6, "NOVELS", "a clue", "")])

    def test_notes_that_are_short_or_of_another_notetype_are_skipped(self) -> None:
        self.insert(7, FIELD_SEPARATOR.join(["only", "three", "fields"]))
        self.insert(
            8, jeopardy_fields("NOVELS", "Emma"), notetype=JEOPARDY_NOTETYPE_ID + 1
        )
        self.assertEqual(read_cards(self.conn), [])


if __name__ == "__main__":
    unittest.main()
