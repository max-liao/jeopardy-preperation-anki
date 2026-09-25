"""Tests for verify_refresh.py: it must ignore what a refresh legitimately rewrites
and catch anything that touches study data, cards, or note content."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from db_fixtures import (
    FIELD_SEPARATOR,
    add_reviewed_jeopardy_note,
    create_collection_schema,
)
from jeopardy_consts import FIELD_ANSWER, TOTAL_FIELDS
from verify_refresh import (
    Fingerprint,
    differences,
    load_snapshot,
    save_snapshot,
    take_fingerprint,
)

OTHER_NOTETYPE_ID = 42


class VerifyRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        create_collection_schema(self.conn)
        add_reviewed_jeopardy_note(self.conn, 1, "NOVELS", "Emma")
        add_reviewed_jeopardy_note(self.conn, 2, "MOVIES", "Rocky")
        self.conn.execute(
            "INSERT INTO notes VALUES (3, 'g3', ?, 1, 0, 'keep', 'front\x1fback', "
            "'front', 0, 0, '')",
            (OTHER_NOTETYPE_ID,),
        )
        self.conn.commit()
        self.before = take_fingerprint(self.conn)

    def changed_components(self) -> list[str]:
        return [
            problem.split(":")[0]
            for problem in differences(self.before, take_fingerprint(self.conn))
        ]

    def test_untouched_collection_has_no_differences(self) -> None:
        self.assertEqual(self.changed_components(), [])

    def test_frequency_fields_and_tags_may_change(self) -> None:
        for note_id in (1, 2):
            (flds,) = self.conn.execute(
                "SELECT flds FROM notes WHERE id = ?", (note_id,)
            ).fetchone()
            extended = (
                flds + FIELD_SEPARATOR + "<b>99</b>" + FIELD_SEPARATOR + "details"
            )
            self.conn.execute(
                "UPDATE notes SET flds = ?, tags = ' freq:high subject:Literature ' "
                "WHERE id = ?",
                (extended, note_id),
            )
        self.assertEqual(self.changed_components(), [])

    def test_a_deleted_review_is_detected(self) -> None:
        self.conn.execute("DELETE FROM revlog WHERE cid = 10")
        self.assertEqual(self.changed_components(), ["revlog"])

    def test_a_rewritten_review_is_detected_even_when_the_count_matches(self) -> None:
        self.conn.execute("UPDATE revlog SET ease = 1 WHERE cid = 10")
        self.assertEqual(self.changed_components(), ["revlog"])

    def test_changed_scheduling_is_detected(self) -> None:
        self.conn.execute("UPDATE cards SET ivl = 1, due = 5 WHERE nid = 1")
        self.assertEqual(self.changed_components(), ["cards"])

    def test_changed_card_content_is_detected(self) -> None:
        (flds,) = self.conn.execute("SELECT flds FROM notes WHERE id = 1").fetchone()
        parts = flds.split(FIELD_SEPARATOR)
        parts[FIELD_ANSWER] = "Persuasion"
        self.conn.execute(
            "UPDATE notes SET flds = ? WHERE id = 1", (FIELD_SEPARATOR.join(parts),)
        )
        self.assertEqual(self.changed_components(), ["notes"])

    def test_the_last_native_field_is_covered(self) -> None:
        (flds,) = self.conn.execute("SELECT flds FROM notes WHERE id = 1").fetchone()
        parts = flds.split(FIELD_SEPARATOR)
        parts[TOTAL_FIELDS - 1] = "tampered"
        self.conn.execute(
            "UPDATE notes SET flds = ? WHERE id = 1", (FIELD_SEPARATOR.join(parts),)
        )
        self.assertEqual(self.changed_components(), ["notes"])

    def test_other_notetypes_are_covered_including_their_tags(self) -> None:
        self.conn.execute("UPDATE notes SET tags = 'changed' WHERE id = 3")
        self.assertEqual(self.changed_components(), ["notes"])

    def test_a_deleted_card_is_detected(self) -> None:
        self.conn.execute("DELETE FROM cards WHERE nid = 2")
        self.assertIn("cards", self.changed_components())

    def test_snapshot_round_trips_through_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            save_snapshot(path, self.before)
            restored: Fingerprint = load_snapshot(path)
        self.assertEqual(restored, self.before)


if __name__ == "__main__":
    unittest.main()
