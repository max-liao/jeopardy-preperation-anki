import sqlite3
import tempfile
import unittest
from pathlib import Path

from decks.trivia.jeopardy.jeopardy_db_helpers import connect_anki, reset_review_progress


class ResetReviewProgressTests(unittest.TestCase):
    def test_reset_review_progress_clears_progress_but_keeps_cards(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.anki2"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE cards (
                    id INTEGER PRIMARY KEY,
                    nid INTEGER,
                    did INTEGER,
                    ord INTEGER,
                    mod INTEGER,
                    usn INTEGER,
                    type INTEGER,
                    queue INTEGER,
                    due INTEGER,
                    ivl INTEGER,
                    factor INTEGER,
                    reps INTEGER,
                    lapses INTEGER,
                    left INTEGER,
                    odue INTEGER,
                    odid INTEGER,
                    flags INTEGER,
                    data TEXT
                )
                """
            )
            conn.execute(
                "CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)"
            )
            conn.execute(
                "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, 10, 20, 0, 1, 0, 1, 2, 42, 7, 2500, 12, 3, 5, 99, 0, 0, "data"),
            )
            conn.execute(
                "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, 1, 0, 2, 7, 5, 2500, 1000, 1),
            )
            conn.commit()
            reset_review_progress(conn)
            card = conn.execute(
                "SELECT queue, type, due, ivl, factor, reps, lapses, left, odue, odid FROM cards WHERE id = 1"
            ).fetchone()
            self.assertEqual(card, (0, 0, 1, 0, 2500, 0, 0, 0, 0, 0))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM revlog").fetchone()[0], 0)
            conn.close()


if __name__ == "__main__":
    unittest.main()
