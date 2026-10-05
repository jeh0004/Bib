CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT,full_name TEXT,is_active INTEGER DEFAULT 1);
CREATE TABLE books(id INTEGER PRIMARY KEY,title TEXT,author TEXT,total_copies INTEGER DEFAULT 1);
CREATE TABLE loans(id INTEGER PRIMARY KEY,book_id INTEGER,user_id INTEGER,status TEXT,reserved_at TEXT,borrowed_at TEXT,due_date TEXT,returned_at TEXT,notes TEXT);
