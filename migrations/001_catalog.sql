CREATE TABLE IF NOT EXISTS book_copies (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
 copy_number INTEGER NOT NULL,
 inventory_code TEXT UNIQUE,
 barcode TEXT UNIQUE,
 qr_token TEXT NOT NULL UNIQUE,
 location TEXT,
 shelf TEXT,
 condition TEXT NOT NULL DEFAULT 'good' CHECK(condition IN ('new','good','worn','damaged')),
 notes TEXT,
 is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
 created_at TEXT NOT NULL DEFAULT (datetime('now')),
 UNIQUE(book_id,copy_number)
);
CREATE INDEX IF NOT EXISTS idx_copies_book ON book_copies(book_id);
CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS genres (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS book_tags (book_id INTEGER NOT NULL REFERENCES books(id), tag_id INTEGER NOT NULL REFERENCES tags(id), PRIMARY KEY(book_id,tag_id));
CREATE TABLE IF NOT EXISTS book_genres (book_id INTEGER NOT NULL REFERENCES books(id), genre_id INTEGER NOT NULL REFERENCES genres(id), PRIMARY KEY(book_id,genre_id));
CREATE TABLE IF NOT EXISTS members (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER UNIQUE REFERENCES users(id),
 member_number TEXT NOT NULL UNIQUE,
 phone TEXT,
 address TEXT,
 postal_code TEXT,
 city TEXT,
 notes TEXT,
 joined_at TEXT NOT NULL DEFAULT (datetime('now')),
 is_active INTEGER NOT NULL DEFAULT 1
);
