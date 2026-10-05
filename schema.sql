CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT    NOT NULL UNIQUE,
    password_hash        TEXT    NOT NULL DEFAULT '',
    full_name            TEXT    NOT NULL,
    email                TEXT,
    role                 TEXT    NOT NULL DEFAULT 'user',
    is_active            INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS books (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    author       TEXT    NOT NULL,
    isbn         TEXT,
    category     TEXT,
    description  TEXT,
    total_copies INTEGER NOT NULL DEFAULT 1,
    added_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS loans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    status      TEXT    NOT NULL DEFAULT 'reserved',
    reserved_at TEXT    NOT NULL DEFAULT (datetime('now')),
    borrowed_at TEXT,
    due_date    TEXT,
    returned_at TEXT,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    club_name     TEXT    NOT NULL DEFAULT 'Mein Verein',
    primary_color TEXT    NOT NULL DEFAULT '#0d6efd',
    logo_filename TEXT
);
