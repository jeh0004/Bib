"""SQLite-backed, process-shared registration limiter.

All POSTs are counted (including successful registrations and invalid CAPTCHAs).
Limits are per client IP; behind a reverse proxy, configure REMOTE_ADDR correctly.
"""
import sqlite3
import time

WINDOW_SECONDS = 900
MAX_ATTEMPTS = 5


def consume_registration_attempt(database, ip, now=None):
    timestamp = int(time.time() if now is None else now)
    # Do not trust user-controlled forwarded headers.
    ip = str(ip)[:128]
    db = sqlite3.connect(database, timeout=15, isolation_level=None)
    try:
        db.execute('PRAGMA busy_timeout=15000')
        db.execute('BEGIN IMMEDIATE')
        db.execute("""CREATE TABLE IF NOT EXISTS registration_attempts
                      (id INTEGER PRIMARY KEY, client_ip TEXT NOT NULL,
                       created_at INTEGER NOT NULL)""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_registration_attempts_ip_time
                      ON registration_attempts(client_ip, created_at)""")
        db.execute('DELETE FROM registration_attempts WHERE created_at <= ?', (timestamp - WINDOW_SECONDS,))
        count = db.execute(
            'SELECT COUNT(*) FROM registration_attempts WHERE client_ip=? AND created_at > ?',
            (ip, timestamp - WINDOW_SECONDS)).fetchone()[0]
        if count >= MAX_ATTEMPTS:
            db.execute('COMMIT')
            return False
        db.execute('INSERT INTO registration_attempts(client_ip,created_at) VALUES(?,?)',
                   (ip, timestamp))
        db.execute('COMMIT')
        return True
    except Exception:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
    finally:
        db.close()
