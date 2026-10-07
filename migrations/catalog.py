"""Idempotent migration for existing LeihGut SQLite databases."""
from pathlib import Path
import secrets
import sqlite3
import json

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
            columns.add('cover_url')
        for column, definition in [('publisher','TEXT'),('published','TEXT'),('pages','INTEGER'),
                                   ('area','TEXT'),('topic','TEXT'),('book_index','TEXT'),
                                   ('cover_source','TEXT'),('cover_status',"TEXT NOT NULL DEFAULT 'missing'"),
                                   ('cover_confidence','INTEGER'),('cover_checked_at','TEXT')]:
            if column not in columns:
                db.execute(f'ALTER TABLE books ADD COLUMN {column} {definition}')
                columns.add(column)
        # Normalize structured metadata from legacy JSON descriptions once, while
        # retaining the original description for backwards compatibility. Some
        # lower-level migration tests intentionally use a reduced legacy books table.
        if 'description' in columns:
            for row in db.execute("SELECT id,description,area,topic,book_index,publisher,published FROM books").fetchall():
                raw = row['description'] if hasattr(row, 'keys') else row[1]
                if not raw or not str(raw).strip().startswith('{'):
                    continue
                try:
                    meta = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(meta, dict):
                    continue
                area = row['area'] if hasattr(row, 'keys') else row[2]
                topic = row['topic'] if hasattr(row, 'keys') else row[3]
                book_index = row['book_index'] if hasattr(row, 'keys') else row[4]
                publisher = row['publisher'] if hasattr(row, 'keys') else row[5]
                published = row['published'] if hasattr(row, 'keys') else row[6]
                db.execute("""UPDATE books SET area=?,topic=?,book_index=?,publisher=?,published=?
                              WHERE id=?""", (
                    area or meta.get('Gebietsthema') or None,
                    topic or meta.get('Sachthema') or None,
                    book_index or meta.get('Buchindex') or None,
                    publisher or meta.get('Verlag') or None,
                    published or meta.get('Auflagedatum') or None,
                    row['id'] if hasattr(row, 'keys') else row[0]
                ))
        db.execute("""CREATE TABLE IF NOT EXISTS cover_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            cover_url TEXT NOT NULL,
            source TEXT NOT NULL,
            candidate_title TEXT,
            candidate_author TEXT,
            candidate_isbn TEXT,
            source_url TEXT,
            confidence INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(book_id,cover_url)
        )""")
        candidate_columns={r[1] for r in db.execute('PRAGMA table_info(cover_candidates)')}
        if 'source_url' not in candidate_columns:
            db.execute('ALTER TABLE cover_candidates ADD COLUMN source_url TEXT')
        db.execute("CREATE INDEX IF NOT EXISTS idx_cover_candidates_book_status ON cover_candidates(book_id,status)")
        if 'cover_url' in columns:
            db.execute("""UPDATE books
                          SET cover_status=CASE
                              WHEN cover_url IS NOT NULL AND TRIM(cover_url)!='' AND
                                   (cover_status IS NULL OR cover_status='missing') THEN 'existing'
                              WHEN cover_status IS NULL OR TRIM(cover_status)='' THEN 'missing'
                              ELSE cover_status END""")

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
