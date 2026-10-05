"""Idempotent migration for existing LeihGut SQLite databases."""
from pathlib import Path
import secrets
import sqlite3

SQL = Path(__file__).with_name('001_catalog.sql')


def migrate(db: sqlite3.Connection):
    # Avoid executescript(): it implicitly commits and would break atomic migration.
    statements = [s.strip() for s in SQL.read_text(encoding='utf-8').split(';') if s.strip()]
    with db:
        for statement in statements:
            db.execute(statement)
        columns = {r[1] for r in db.execute('PRAGMA table_info(books)')}
        if 'cover_url' not in columns:
            db.execute('ALTER TABLE books ADD COLUMN cover_url TEXT')
        loan_columns = {r[1] for r in db.execute('PRAGMA table_info(loans)')}
        if 'copy_id' not in loan_columns:
            db.execute('ALTER TABLE loans ADD COLUMN copy_id INTEGER REFERENCES book_copies(id)')
        # Existing book-level loans are preserved, without guessing which copy they used.
        for book_id, total in db.execute('SELECT id, total_copies FROM books').fetchall():
            existing = {r[0] for r in db.execute(
                'SELECT copy_number FROM book_copies WHERE book_id=?', (book_id,))}
            for number in range(1, max(0, total) + 1):
                if number not in existing:
                    db.execute('INSERT INTO book_copies '
                               '(book_id,copy_number,inventory_code,qr_token) VALUES (?,?,?,?)',
                               (book_id, number, f'LG-{book_id}-{number}', secrets.token_urlsafe(18)))
