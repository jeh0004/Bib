"""Idempotent phase 3 loan schema migration."""
def migrate(db):
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS loan_policy (
            id INTEGER PRIMARY KEY CHECK(id=1), loan_days INTEGER NOT NULL DEFAULT 28
            CHECK(loan_days BETWEEN 1 AND 365), max_renewals INTEGER NOT NULL DEFAULT 2
            CHECK(max_renewals BETWEEN 0 AND 20), reservation_days INTEGER NOT NULL DEFAULT 7
            CHECK(reservation_days BETWEEN 1 AND 60))''')
        db.execute('INSERT OR IGNORE INTO loan_policy(id) VALUES(1)')
        policy_cols={r[1] for r in db.execute('PRAGMA table_info(loan_policy)')}
        if 'reservation_days' not in policy_cols:
            db.execute('ALTER TABLE loan_policy ADD COLUMN reservation_days INTEGER NOT NULL DEFAULT 7')
        cols={r[1] for r in db.execute('PRAGMA table_info(loans)')}
        if 'renewal_count' not in cols:
            db.execute('ALTER TABLE loans ADD COLUMN renewal_count INTEGER NOT NULL DEFAULT 0')
        if 'renewal_requested_at' not in cols:
            db.execute('ALTER TABLE loans ADD COLUMN renewal_requested_at TEXT')
        if 'reservation_expires_at' not in cols:
            db.execute('ALTER TABLE loans ADD COLUMN reservation_expires_at TEXT')
        db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_loan_per_copy
            ON loans(copy_id) WHERE copy_id IS NOT NULL AND status IN ('reserved','borrowed')''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_loans_due ON loans(status,due_date)')

        db.execute("""CREATE TABLE IF NOT EXISTS loan_waitlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            requested_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(book_id,user_id)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_waitlist_order ON loan_waitlist(book_id,requested_at,id)")

        db.execute("""CREATE TABLE IF NOT EXISTS circulation_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_id INTEGER,
            book_id INTEGER,
            user_id INTEGER,
            staff_user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_circulation_audit_created ON circulation_audit(created_at,id)")
