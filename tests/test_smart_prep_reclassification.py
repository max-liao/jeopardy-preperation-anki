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

from consolidate_taxonomy import MANUAL_OVERRIDES
from db_fixtures import (
    FIELD_SEPARATOR,
    add_reviewed_jeopardy_note,
    create_collection_schema,
)
from jeopardy_consts import EVIDENCE_REPORT_COLUMNS, THEME_WINDOW_YEARS, TOTAL_FIELDS
from smart_prep import (
    apply_scores_and_tags,
    build_recent_subject_counts,
    compute_scores,
)
from verify_refresh import differences, take_fingerprint

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
}
# The secondary_subject the name-only pass gave two of the wordplay categories.
SECONDARY = {RHYME_CATEGORY: "Science", A_CATEGORY: "Geography"}
TAXONOMY_JSON = {
    category: {
        "subject": subject,
        "sub_category": sub_category,
        "secondary_subject": SECONDARY.get(category, ""),
    }
    for category, ((subject, sub_category), _answers, _clue) in DECK.items()
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
        self.conn.commit()

    def refresh(self, evidence_report: Path | None = None) -> None:
        """Run the same steps as `smart_prep.py --live-db`, minus the notetype surgery."""
        meta, scored, _subject, _secondary = compute_scores(
            self.conn, self.taxonomy_path, evidence_report
        )
        latest_year = max(item[5] for item in meta.values())
        counts, start_year = build_recent_subject_counts(meta, latest_year)
        apply_scores_and_tags(
            self.conn,
            meta,
            scored,
            SCORE_ORD,
            DETAILS_ORD,
            latest_year,
            counts,
            start_year,
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

    def test_grab_bag_and_opera_about_novels_keep_showing_other(self) -> None:
        self.refresh()
        for category in ("A LITTLE OF EVERYTHING", "RUSSIAN OPERA"):
            for note_id in self.note_ids[category]:
                with self.subTest(category=category, note_id=note_id):
                    self.assertIn(
                        "Frequently asked theme: Other", self.details_text(note_id)
                    )
                    self.assertIn("subject:Other", self.tags(note_id))

    def test_manually_pinned_category_is_not_moved_even_with_literary_cards(
        self,
    ) -> None:
        self.assertIn(PINNED_CATEGORY, MANUAL_OVERRIDES)
        self.refresh()
        for note_id in self.note_ids[PINNED_CATEGORY]:
            with self.subTest(note_id=note_id):
                self.assertIn(
                    "Frequently asked theme: Other", self.details_text(note_id)
                )
                self.assertIn("subject:Other", self.tags(note_id))

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
            },
        )

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


if __name__ == "__main__":
    unittest.main()
