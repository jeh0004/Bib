"""Regression tests for reservation capacity and concurrency safety."""
import sqlite3
import unittest

from app import create_reservation, get_available_copies


def database():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE books(id INTEGER PRIMARY KEY, total_copies INTEGER);
        CREATE TABLE book_copies(id INTEGER PRIMARY KEY, book_id INTEGER, is_active INTEGER);
        CREATE TABLE loan_waitlist(id INTEGER PRIMARY KEY, book_id INTEGER, user_id INTEGER, requested_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE loans(
            id INTEGER PRIMARY KEY, book_id INTEGER, user_id INTEGER,
            status TEXT, reserved_at TEXT
        );
        INSERT INTO books VALUES (1, 2);
        INSERT INTO book_copies VALUES (1, 1, 1), (2, 1, 0);
    """)
    return db


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.db = database()

    def tearDown(self):
        self.db.close()

    def test_inactive_copies_not_available(self):
        self.assertEqual(get_available_copies(self.db, 1), 1)

    def test_capacity_and_duplicate_reservation(self):
        create_reservation(self.db, 1, 10)
        self.assertEqual(get_available_copies(self.db, 1), 0)
        with self.assertRaisesRegex(ValueError, "already_reserved"):
            create_reservation(self.db, 1, 10)
        with self.assertRaisesRegex(ValueError, "not_available"):
            create_reservation(self.db, 1, 11)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM loans").fetchone()[0], 1)

    def test_waitlist_priority(self):
        self.db.execute("INSERT INTO loan_waitlist(book_id,user_id) VALUES(1,12)")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "waitlist_priority"):
            create_reservation(self.db, 1, 10)
        create_reservation(self.db, 1, 12)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM loan_waitlist").fetchone()[0], 0)

    def test_legacy_total_copies_limit(self):
        self.db.execute("UPDATE books SET total_copies=0 WHERE id=1")
        self.db.commit()
        self.assertEqual(get_available_copies(self.db, 1), 0)


if __name__ == "__main__":
    unittest.main()
