import sqlite3
import json
import os
import re
import random
import secrets
import time
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (
    Flask, g, session, redirect, url_for, request,
    render_template, flash, abort, send_from_directory, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from translations import STRINGS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s'
)
security_log = logging.getLogger('security')

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)

from catalog_extra import bp as catalog_extra_bp
from loans_extra import bp as loans_extra_bp
from book_import import bp as book_import_bp
app.register_blueprint(catalog_extra_bp)
app.register_blueprint(loans_extra_bp)
app.register_blueprint(book_import_bp)

DATABASE = os.environ.get('DATABASE', '/data/vereinsbibliothek.db')

# ---------------------------------------------------------------------------
# Secret key: auto-generate and persist to /data if not provided via env
# ---------------------------------------------------------------------------

_KNOWN_BAD_KEYS = {
    'change-me-in-production',
    'bitte-aendern-vor-produktionsbetrieb',
    '',
}

def _get_secret_key():
    env_key = os.environ.get('SECRET_KEY', '')
    if env_key not in _KNOWN_BAD_KEYS:
        return env_key
    key_file = os.path.join(os.path.dirname(DATABASE), '.secret_key')
    try:
        with open(key_file) as f:
            key = f.read().strip()
            if key:
                return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, 'w') as f:
            f.write(key)
        security_log.info("Generated new secret key and saved to %s", key_file)
    except OSError:
        security_log.warning("Could not persist secret key; will regenerate on restart")
    return key

app.secret_key = _get_secret_key()

# ---------------------------------------------------------------------------
# Session / cookie security
# ---------------------------------------------------------------------------

UPLOAD_DIR = os.environ.get('UPLOAD_DIR', '/data/uploads')
ALLOWED_LOGO_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'}

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', 'false').lower() == 'true',
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=60),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,  # 2 MB upload limit
)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_e=None):
    db = g.pop('db', None)
    if db:
        db.close()


def _add_column_if_missing(db, table, column, definition):
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        db.commit()
    except sqlite3.OperationalError:
        pass  # column already exists — safe to ignore


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def hex_to_rgb(hex_color):
    """Convert #rrggbb to 'r,g,b' for CSS custom properties."""
    h = hex_color.lstrip('#')
    if len(h) != 6:
        return '13,110,253'  # Bootstrap blue fallback
    return f"{int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)}"


def get_settings(db):
    return db.execute("SELECT * FROM settings WHERE id = 1").fetchone()


@app.context_processor
def inject_settings():
    lang = 'de'
    session['lang'] = 'de'
    t = STRINGS['de']
    try:
        db = get_db()
        s = get_settings(db)
        color = s['primary_color'] if s else '#0d6efd'
        return {
            'site_settings': s,
            'primary_rgb': hex_to_rgb(color),
            't': t,
            'lang': lang,
        }
    except Exception:
        return {'site_settings': None, 'primary_rgb': '13,110,253', 't': t, 'lang': lang}


@app.template_filter('de_date')
def de_date(value):
    """Render ISO dates/timestamps as DD.MM.YYYY without changing stored values."""
    if not value:
        return '–'
    try:
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).strftime('%d.%m.%Y')
    except (ValueError, TypeError):
        try:
            return datetime.strptime(str(value)[:10], '%Y-%m-%d').strftime('%d.%m.%Y')
        except (ValueError, TypeError):
            return str(value)


def flash_msg(key, category='info', **kwargs):
    """Flash a translated message looked up from STRINGS by key."""
    lang = 'de'
    strings = STRINGS['de']
    msg = strings.get(key, STRINGS['en'].get(key, key))
    if kwargs:
        msg = msg.format(**kwargs)
    flash(msg, category)


@app.route('/set-language/<lang>')
def set_language(lang):
    # The production UI is intentionally German-only so every workflow uses
    # one complete and consistent vocabulary.
    session['lang'] = 'de'
    return redirect(request.referrer or url_for('login'))


@app.route('/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


def init_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql') as f:
            db.executescript(f.read().decode())
        from migrations.catalog import migrate as migrate_catalog
        from migrations.loans import migrate as migrate_loans
        from migrations.data_cleanup import migrate as migrate_data_cleanup
        from migrations.data_cleanup_v2 import migrate as migrate_data_cleanup_v2
        migrate_catalog(db)
        migrate_loans(db)
        migrate_data_cleanup(db)
        migrate_data_cleanup_v2(db)
        _add_column_if_missing(db, 'users', 'must_change_password', 'INTEGER NOT NULL DEFAULT 0')
        _add_column_if_missing(db, 'loans', 'renewal_requested_at', 'TEXT')
        # Ensure settings singleton row exists
        db.execute(
            "INSERT OR IGNORE INTO settings (id, club_name, primary_color) VALUES (1, 'Mein Verein', '#0d6efd')"
        )
        db.commit()
        if not db.execute("SELECT 1 FROM users").fetchone():
            db.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, email, role, must_change_password)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                ('admin', generate_password_hash('admin'),
                 'Administrator', 'admin@vereinsbibliothek.local', 'admin')
            )
            db.commit()

# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------

def validate_password(pw):
    lang = 'de'
    t = STRINGS['de']
    errors = []
    if len(pw) < 12:
        errors.append(t['pw_req_length'] + '.')
    if not re.search(r'[A-Z]', pw) or not re.search(r'[a-z]', pw):
        errors.append(t['pw_req_case'] + '.')
    if not re.search(r'\d', pw):
        errors.append(t['pw_req_digit'] + '.')
    if not re.search(r'[^A-Za-z0-9]', pw):
        errors.append(t['pw_req_special'])
    return errors


def generate_initial_password():
    lower   = 'abcdefghjkmnpqrstuvwxyz'
    upper   = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
    digits  = '23456789'
    special = '!@#$%&*'
    all_chars = lower + upper + digits + special
    pw = [
        secrets.choice(lower),
        secrets.choice(upper),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    pw += [secrets.choice(all_chars) for _ in range(8)]
    random.shuffle(pw)
    return ''.join(pw)

# ---------------------------------------------------------------------------
# CAPTCHA helpers
# ---------------------------------------------------------------------------

def new_captcha():
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    session['captcha_ans'] = a + b
    return a, b


def check_captcha():
    expected = session.pop('captcha_ans', None)
    given = request.form.get('captcha', '').strip()
    return expected is not None and given.isdigit() and int(given) == expected

# ---------------------------------------------------------------------------
# Login rate limiting (in-memory, per username)
# ---------------------------------------------------------------------------

_login_attempts: dict[str, list] = defaultdict(list)
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 900  # 15 minutes


def _is_rate_limited(username: str) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_LOGIN_LOCKOUT_SECONDS)
    # Remove expired attempts
    _login_attempts[username] = [
        t for t in _login_attempts[username] if t > cutoff
    ]
    return len(_login_attempts[username]) >= _LOGIN_MAX_ATTEMPTS


def _record_failed_attempt(username: str):
    _login_attempts[username].append(datetime.now(timezone.utc))


def _clear_attempts(username: str):
    _login_attempts.pop(username, None)

# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------

@app.before_request
def ensure_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)


@app.before_request
def verify_csrf_token():
    # API routes use a custom header instead of CSRF tokens
    if request.path.startswith('/api/'):
        if request.headers.get('X-LeihGut-Client') != 'ios':
            return jsonify(error='Forbidden'), 403
        return
    if request.method == 'POST':
        token = request.form.get('csrf_token', '')
        session_token = session.get('csrf_token', '')
        if not session_token or not secrets.compare_digest(token, session_token):
            security_log.warning(
                "CSRF validation failed | path=%s ip=%s",
                request.path, request.remote_addr
            )
            flash_msg('flash_csrf_error', 'danger')
            return redirect(url_for('login'))

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

@app.after_request
def set_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data: https:; "
        "frame-ancestors 'none';"
    )
    return response

# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        current = get_db().execute('SELECT role,is_active FROM users WHERE id=?', (session['user_id'],)).fetchone()
        if not current or not current['is_active']:
            session.clear()
            return redirect(url_for('login'))
        session['role'] = current['role']
        if session.get('must_change_password') and request.endpoint != 'change_password':
            flash_msg('flash_must_change_pw', 'warning')
            return redirect(url_for('change_password'))
        return f(*args, **kwargs)
    return decorated


def librarian_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if session.get('role') not in ('admin', 'librarian'):
            abort(403)
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        current = get_db().execute('SELECT role,is_active FROM users WHERE id=?', (session.get('user_id'),)).fetchone()
        if not current or not current['is_active'] or current['role'] != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def sync_book_copies(db, book_id, requested):
    """Maintain copy rows for legacy book edit forms without losing loan history."""
    import secrets
    rows = db.execute(
        "SELECT id,copy_number,is_active FROM book_copies WHERE book_id=? ORDER BY copy_number",
        (book_id,)
    ).fetchall()
    active_loans = db.execute(
        "SELECT COUNT(*) FROM loans WHERE book_id=? AND status IN ('reserved','borrowed')",
        (book_id,)
    ).fetchone()[0]
    if requested < active_loans:
        raise ValueError("Anzahl kleiner als aktive Ausleihen/Reservierungen")
    active = [r for r in rows if r['is_active']]
    if requested > len(active):
        inactive = [r for r in rows if not r['is_active']]
        for row in inactive[:requested-len(active)]:
            db.execute("UPDATE book_copies SET is_active=1 WHERE id=?", (row['id'],))
        remaining = requested - len(active) - min(len(inactive), requested-len(active))
        next_num = max((r['copy_number'] for r in rows), default=0)
        for i in range(remaining):
            next_num += 1
            db.execute("INSERT INTO book_copies(book_id,copy_number,inventory_code,qr_token) VALUES(?,?,?,?)",
                       (book_id,next_num,f'LG-{book_id}-{next_num}',secrets.token_urlsafe(18)))
    elif requested < len(active):
        busy = {r[0] for r in db.execute(
            "SELECT copy_id FROM loans WHERE book_id=? AND status IN ('reserved','borrowed') AND copy_id IS NOT NULL",
            (book_id,))}
        removable = [r for r in reversed(active) if r['id'] not in busy]
        count = len(active)-requested
        if len(removable) < count:
            raise ValueError("Exemplare mit aktiver Ausleihe können nicht deaktiviert werden")
        for row in removable[:count]:
            db.execute("UPDATE book_copies SET is_active=0 WHERE id=?", (row['id'],))
    db.execute("UPDATE books SET total_copies=? WHERE id=?", (requested,book_id))


def get_available_copies(db, book_id):
    """Shared capacity rule for legacy reservations and physical copies."""
    book = db.execute("SELECT total_copies FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        return 0
    physical = db.execute(
        "SELECT COUNT(*) FROM book_copies WHERE book_id = ? AND is_active = 1",
        (book_id,)
    ).fetchone()[0]
    active = db.execute(
        "SELECT COUNT(*) FROM loans WHERE book_id = ? AND status IN ('reserved', 'borrowed')",
        (book_id,)
    ).fetchone()[0]
    return max(0, min(book['total_copies'], physical) - active)

def audit_circulation(db, action, loan_id=None, book_id=None, user_id=None, details=None):
    """Record staff circulation actions without coupling business logic to the UI."""
    db.execute("""INSERT INTO circulation_audit
        (loan_id,book_id,user_id,staff_user_id,action,details)
        VALUES(?,?,?,?,?,?)""",
        (loan_id,book_id,user_id,session.get('user_id'),action,details))


def expire_reservations(db):
    """Release pickup reservations whose configured collection period elapsed."""
    expired=db.execute("""SELECT id,book_id,user_id FROM loans
        WHERE status='reserved' AND reservation_expires_at IS NOT NULL
          AND reservation_expires_at < datetime('now')""").fetchall()
    if not expired:
        return 0
    with db:
        for row in expired:
            db.execute("UPDATE loans SET status='cancelled' WHERE id=? AND status='reserved'",(row['id'],))
            db.execute("""INSERT INTO circulation_audit
                (loan_id,book_id,user_id,staff_user_id,action,details)
                VALUES(?,?,?,?,?,?)""",(row['id'],row['book_id'],row['user_id'],None,'reservation_expired','Abholfrist abgelaufen'))
    return len(expired)


def renewal_block_reason(db, loan):
    """Return a user-facing reason when a borrowed loan cannot be renewed."""
    if not loan or loan['status']!='borrowed':
        return 'Keine aktive Ausleihe vorhanden.'
    policy=db.execute("SELECT max_renewals FROM loan_policy WHERE id=1").fetchone()
    if loan['renewal_count'] >= policy['max_renewals']:
        return 'Maximale Anzahl an Verlängerungen erreicht.'
    if db.execute("SELECT 1 FROM loans WHERE book_id=? AND status='reserved' AND id<>?",
                  (loan['book_id'],loan['id'])).fetchone():
        return 'Eine Reservierung für dieses Buch liegt bereits vor.'
    if db.execute("SELECT 1 FROM loan_waitlist WHERE book_id=?",(loan['book_id'],)).fetchone():
        return 'Eine andere Person wartet bereits auf dieses Buch.'
    return None


def create_reservation(db, book_id, user_id):
    """Atomically reserve a free slot for both web and API requests."""
    db.execute('BEGIN IMMEDIATE')
    try:
        existing = db.execute(
            "SELECT 1 FROM loans WHERE book_id=? AND user_id=? AND status IN ('reserved','borrowed')",
            (book_id, user_id)
        ).fetchone()
        if existing:
            raise ValueError('already_reserved')
        if get_available_copies(db, book_id) <= 0:
            raise ValueError('not_available')
        first = db.execute(
            "SELECT user_id FROM loan_waitlist WHERE book_id=? ORDER BY requested_at,id LIMIT 1",
            (book_id,)
        ).fetchone()
        if first and first['user_id'] != user_id:
            raise ValueError('waitlist_priority')
        try:
            reservation_days=db.execute("SELECT reservation_days FROM loan_policy WHERE id=1").fetchone()[0]
        except sqlite3.OperationalError:
            reservation_days=7
        loan_cols={r[1] for r in db.execute('PRAGMA table_info(loans)')}
        if 'reservation_expires_at' in loan_cols:
            db.execute(
                """INSERT INTO loans (book_id,user_id,status,reserved_at,reservation_expires_at)
                   VALUES (?,?,'reserved',datetime('now'),datetime('now', ?))""",
                (book_id, user_id, f'+{reservation_days} days')
            )
        else:
            db.execute(
                "INSERT INTO loans (book_id,user_id,status,reserved_at) VALUES (?,?,'reserved',datetime('now'))",
                (book_id,user_id)
            )
        if first:
            db.execute("DELETE FROM loan_waitlist WHERE book_id=? AND user_id=?", (book_id,user_id))
        db.commit()
    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('catalog'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()

        if _is_rate_limited(username):
            remaining = _LOGIN_LOCKOUT_SECONDS // 60
            security_log.warning("Login blocked (rate limit) | user=%s ip=%s", username, request.remote_addr)
            flash_msg('flash_rate_limited', 'danger', minutes=remaining)
            a, b = new_captcha()
            return render_template('login.html', captcha_a=a, captcha_b=b)

        if not check_captcha():
            flash_msg('flash_captcha_wrong', 'danger')
            a, b = new_captcha()
            return render_template('login.html', captcha_a=a, captcha_b=b)

        password = request.form.get('password', '')
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            _clear_attempts(username)
            session.clear()
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['full_name'] = user['full_name']
            session['role'] = user['role']
            session['must_change_password'] = bool(user['must_change_password'])
            security_log.info("Login successful | user=%s ip=%s", username, request.remote_addr)
            if user['must_change_password']:
                flash_msg('flash_login_must_change', 'warning')
                return redirect(url_for('change_password'))
            if user['role'] == 'librarian':
                return redirect(url_for('admin_loans'))
            return redirect(url_for('catalog'))

        _record_failed_attempt(username)
        remaining = _LOGIN_MAX_ATTEMPTS - len(_login_attempts[username])
        security_log.warning(
            "Login failed | user=%s ip=%s attempts_left=%d",
            username, request.remote_addr, max(remaining, 0)
        )
        flash_msg('flash_invalid_credentials', 'danger')

    a, b = new_captcha()
    return render_template('login.html', captcha_a=a, captcha_b=b)


# Public registration: pending approval, never creates admins.
def render_registration():
    """Issue a fresh, session-bound registration challenge on each form display."""
    a, b = new_captcha()
    session['registration_started_at'] = time.time()
    return render_template('register.html', captcha_a=a, captcha_b=b)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('catalog'))
    if request.method == 'POST':
        from registration_limit import consume_registration_attempt
        # All registration submissions count, including successful ones.
        # One SQLite write transaction serializes checks across Gunicorn workers.
        if not consume_registration_attempt(DATABASE, request.remote_addr or 'unknown'):
            security_log.warning("Registration throttled | ip=%s", request.remote_addr)
            flash('Zu viele Registrierungsversuche. Bitte in 15 Minuten erneut versuchen.', 'danger')
            return render_registration()
        # A valid form must have been served first, and not submitted instantly.
        started = session.pop('registration_started_at', None)
        too_fast = (not isinstance(started, (int, float))
                    or not 3 <= time.time() - started <= 3600)
        honeypot_filled = bool(request.form.get('contact_website', '').strip())
        if too_fast or honeypot_filled:
            security_log.warning("Registration bot check failed | ip=%s", request.remote_addr)
            flash('Registrierung konnte nicht geprüft werden. Bitte Formular erneut ausfüllen.', 'danger')
            return render_registration()
        username = request.form.get('username', '').strip()
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        pw = request.form.get('password', '')
        confirm = request.form.get('password_confirm', '')
        if not check_captcha():
            flash('Sicherheitsfrage falsch.', 'danger')
        elif (not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}', username)
              or len(full_name) < 2 or len(full_name) > 120
              or len(email) > 254 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email)
              or pw != confirm or validate_password(pw)):
            flash('Angaben ungültig. Passwort: mindestens 12 Zeichen, Groß-/Kleinbuchstaben, Zahl und Sonderzeichen.', 'danger')
        else:
            try:
                with get_db():
                    get_db().execute(
                        """INSERT INTO users(username,password_hash,full_name,email,role,is_active,must_change_password)
                           VALUES(?,?,?,?, 'user',0,0)""",
                        (username, generate_password_hash(pw), full_name, email))
                flash('Registrierung eingegangen. Ein Administrator muss dein Konto freischalten.', 'success')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                    flash('Benutzername bereits vergeben.', 'danger')
    return render_registration()


@app.route('/logout')
def logout():
    security_log.info("Logout | user=%s ip=%s", session.get('username', '?'), request.remote_addr)
    session.clear()
    return redirect(url_for('login'))


@app.route('/my-password', methods=['GET', 'POST'])
@login_required
def my_password():
    """Self-service password change for any logged-in user."""
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        pw = request.form.get('password', '')
        pw2 = request.form.get('password2', '')
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
        if not check_password_hash(user['password_hash'], current):
            flash_msg('flash_pw_wrong_current', 'danger')
            return render_template('my_password.html')
        errors = validate_password(pw)
        if pw != pw2:
            errors.append(STRINGS.get(session.get('lang', 'de'), STRINGS['en'])['flash_pw_mismatch'])
        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('my_password.html')
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(pw), session['user_id'])
        )
        db.commit()
        security_log.info("Password self-changed | user=%s ip=%s", session.get('username'), request.remote_addr)
        flash_msg('flash_pw_changed', 'success')
        return redirect(url_for('catalog'))
    return render_template('my_password.html')


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        pw = request.form.get('password', '')
        pw2 = request.form.get('password2', '')
        errors = validate_password(pw)
        if pw != pw2:
            errors.append(STRINGS.get(session.get('lang', 'de'), STRINGS['en'])['flash_pw_mismatch'])
        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('change_password.html')
        db = get_db()
        db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (generate_password_hash(pw), session['user_id'])
        )
        db.commit()
        session['must_change_password'] = False
        security_log.info("Password changed | user=%s ip=%s", session.get('username'), request.remote_addr)
        flash_msg('flash_pw_changed', 'success')
        return redirect(url_for('catalog'))
    return render_template('change_password.html')

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

@app.route('/')
@login_required
def index():
    if session.get('role') == 'librarian':
        return redirect(url_for('admin_loans'))
    return redirect(url_for('catalog'))


@app.route('/catalog')
@login_required
def catalog():
    db = get_db()
    expire_reservations(db)
    q=request.args.get('q','').strip()
    category=request.args.get('category','').strip()
    author=request.args.get('author','').strip()
    area=request.args.get('area','').strip()
    topic=request.args.get('topic','').strip()
    available_only=request.args.get('available')=='1'
    sort=request.args.get('sort','title')
    sort_sql={
        'title':'title COLLATE NOCASE, author COLLATE NOCASE',
        'author':'author COLLATE NOCASE, title COLLATE NOCASE',
        'year':'published DESC, title COLLATE NOCASE',
        'available':'available DESC, title COLLATE NOCASE',
    }.get(sort,'title COLLATE NOCASE, author COLLATE NOCASE')
    try:
        page=max(1,int(request.args.get('page','1')))
    except ValueError:
        page=1
    per_page=24
    term=f"%{q}%"
    base="""
        SELECT b.*,
               MAX(0, MIN(b.total_copies,
                   (SELECT COUNT(*) FROM book_copies c WHERE c.book_id=b.id AND c.is_active=1))
                   - (SELECT COUNT(*) FROM loans l WHERE l.book_id=b.id
                      AND l.status IN ('reserved','borrowed'))) AS available
        FROM books b
        WHERE (b.title LIKE ? OR b.author LIKE ? OR b.category LIKE ? OR b.isbn LIKE ?
               OR b.publisher LIKE ? OR b.area LIKE ? OR b.topic LIKE ? OR b.book_index LIKE ?
               OR EXISTS (SELECT 1 FROM book_copies c WHERE c.book_id=b.id
                   AND (c.barcode LIKE ? OR c.inventory_code LIKE ? OR c.location LIKE ? OR c.shelf LIKE ?))
               OR EXISTS (SELECT 1 FROM book_tags bt JOIN tags t ON t.id=bt.tag_id
                   WHERE bt.book_id=b.id AND t.name LIKE ?)
               OR EXISTS (SELECT 1 FROM book_genres bg JOIN genres ge ON ge.id=bg.genre_id
                   WHERE bg.book_id=b.id AND ge.name LIKE ?))
          AND (?='' OR b.category=?)
          AND (?='' OR b.author LIKE ?)
          AND (?='' OR b.area LIKE ?)
          AND (?='' OR b.topic LIKE ?)
    """
    params=[term]*14+[category,category,author,f"%{author}%",area,f"%{area}%",topic,f"%{topic}%"]
    wrapped="SELECT * FROM ("+base+") x"
    if available_only:
        wrapped+=" WHERE available>0"
    total=db.execute("SELECT COUNT(*) FROM ("+wrapped+") z",params).fetchone()[0]
    pages=max(1,(total+per_page-1)//per_page)
    page=min(page,pages)
    books=db.execute(wrapped+" ORDER BY "+sort_sql+" LIMIT ? OFFSET ?",
                     params+[per_page,(page-1)*per_page]).fetchall()
    categories=[row[0] for row in db.execute(
        "SELECT DISTINCT category FROM books WHERE category IS NOT NULL AND TRIM(category)<>'' ORDER BY category")]
    authors=[row[0] for row in db.execute(
        "SELECT DISTINCT author FROM books WHERE author IS NOT NULL AND TRIM(author)<>'' AND author<>'Unbekannt' ORDER BY author LIMIT 300")]
    areas=[row[0] for row in db.execute(
        "SELECT DISTINCT area FROM books WHERE area IS NOT NULL AND TRIM(area)<>'' ORDER BY area")]
    topics=[row[0] for row in db.execute(
        "SELECT DISTINCT topic FROM books WHERE topic IS NOT NULL AND TRIM(topic)<>'' ORDER BY topic")]
    return render_template('catalog.html',books=books,q=q,category=category,author=author,
        area=area,topic=topic,available_only=available_only,categories=categories,
        authors=authors,areas=areas,topics=topics,result_count=total,page=page,pages=pages,
        per_page=per_page,sort=sort)



@app.route('/book/<int:book_id>')
@login_required
def book_detail(book_id):
    db = get_db()
    expire_reservations(db)
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        abort(404)
    available = get_available_copies(db, book_id)
    loans = db.execute("""
        SELECT l.*, u.full_name, u.username
        FROM loans l JOIN users u ON l.user_id = u.id
        WHERE l.book_id = ? AND l.status IN ('reserved','borrowed')
        ORDER BY l.reserved_at
    """, (book_id,)).fetchall()
    my_loan = db.execute("""
        SELECT * FROM loans
        WHERE book_id = ? AND user_id = ? AND status IN ('reserved','borrowed')
    """, (book_id, session['user_id'])).fetchone()
    queue = db.execute(
        "SELECT user_id FROM loan_waitlist WHERE book_id=? ORDER BY requested_at,id",
        (book_id,)
    ).fetchall()
    waitlist_position = next((i+1 for i, entry in enumerate(queue)
                              if entry['user_id'] == session['user_id']), None)
    can_reserve = available > 0 and (not queue or queue[0]['user_id'] == session['user_id'])
    description_text=book['description'] or ''
    extra_details={}
    if description_text.strip().startswith('{'):
        try:
            parsed=json.loads(description_text)
            if isinstance(parsed,dict):
                description_text=''
        except (ValueError,TypeError):
            pass
    for label,column in [('Gebiet', 'area'),('Thema','topic'),('Buchindex','book_index')]:
        if book[column]:
            extra_details[label]=book[column]
    back=request.args.get('back','')
    back_url=back if back.startswith('/catalog') else url_for('catalog')
    return render_template('book_detail.html',description_text=description_text,
                           extra_details=extra_details,book=book,available=available,
                           loans=loans,my_loan=my_loan,waitlist_position=waitlist_position,
                           waitlist_count=len(queue),can_reserve=can_reserve,back_url=back_url)


@app.route('/book/<int:book_id>/reserve', methods=['POST'])
@login_required
def reserve_book(book_id):
    db = get_db()
    try:
        create_reservation(db, book_id, session['user_id'])
    except ValueError as exc:
        if str(exc) == 'already_reserved':
            flash_msg('flash_already_reserved', 'warning')
        else:
            flash_msg('flash_not_available', 'danger')
        return redirect(url_for('book_detail', book_id=book_id))
    flash_msg('flash_reserved_ok', 'success')
    return redirect(url_for('my_loans'))


@app.route('/book/<int:book_id>/waitlist', methods=['POST'])
@login_required
def join_waitlist(book_id):
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        if not db.execute("SELECT 1 FROM books WHERE id=?", (book_id,)).fetchone():
            abort(404)
        if db.execute("SELECT 1 FROM loans WHERE book_id=? AND user_id=? AND status IN ('reserved','borrowed')", (book_id,session['user_id'])).fetchone():
            flash('Du hast dieses Buch bereits reserviert oder ausgeliehen.', 'warning')
        elif get_available_copies(db,book_id)>0 and not db.execute("SELECT 1 FROM loan_waitlist WHERE book_id=?", (book_id,)).fetchone():
            flash('Ein Exemplar ist verfügbar. Bitte reserviere es direkt.', 'info')
        else:
            db.execute("INSERT OR IGNORE INTO loan_waitlist(book_id,user_id) VALUES (?,?)",(book_id,session['user_id']))
            flash('Du stehst auf der Warteliste.', 'success')
        db.commit()
    except Exception:
        db.rollback()
        raise
    return redirect(url_for('book_detail',book_id=book_id))


@app.route('/book/<int:book_id>/return', methods=['POST'])
@login_required
def return_book(book_id):
    # Physical returns must be recorded by library staff, never by members.
    abort(403)
    db = get_db()
    loan = db.execute("""
        SELECT * FROM loans
        WHERE book_id = ? AND user_id = ? AND status IN ('reserved','borrowed')
    """, (book_id, session['user_id'])).fetchone()
    if not loan:
        flash_msg('flash_no_active_loan', 'danger')
        return redirect(url_for('catalog'))
    db.execute("""
        UPDATE loans SET status = 'returned', returned_at = datetime('now')
        WHERE id = ?
    """, (loan['id'],))
    db.commit()
    flash_msg('flash_return_ok', 'success')
    return redirect(url_for('my_loans'))

# ---------------------------------------------------------------------------
# User: my loans
# ---------------------------------------------------------------------------

@app.route('/my-loans')
@login_required
def my_loans():
    db=get_db()
    expire_reservations(db)
    rows=db.execute("""SELECT l.*,b.title,b.author,b.cover_url
        FROM loans l JOIN books b ON l.book_id=b.id
        WHERE l.user_id=? AND l.status IN ('reserved','borrowed')
        ORDER BY l.reserved_at DESC""",(session['user_id'],)).fetchall()
    today=datetime.now(timezone.utc).date()
    active=[]
    for row in rows:
        item=dict(row)
        item['renewal_block_reason']=renewal_block_reason(db,row) if row['status']=='borrowed' else None
        item['overdue_days']=0
        if row['status']=='borrowed' and row['due_date']:
            try:
                due=datetime.fromisoformat(row['due_date']).date()
                item['overdue_days']=max(0,(today-due).days)
            except ValueError:
                pass
        active.append(item)
    history=db.execute("""SELECT l.*,b.title,b.author FROM loans l JOIN books b ON l.book_id=b.id
        WHERE l.user_id=? AND l.status='returned' ORDER BY l.returned_at DESC LIMIT 20""",
        (session['user_id'],)).fetchall()
    return render_template('user/my_loans.html',active=active,history=history)

@app.route('/my-loans/<int:loan_id>/request-renewal', methods=['POST'])
@login_required
def request_renewal(loan_id):
    db=get_db()
    loan=db.execute("SELECT * FROM loans WHERE id=? AND user_id=? AND status='borrowed'",
                    (loan_id,session['user_id'])).fetchone()
    reason=renewal_block_reason(db,loan)
    if not loan:
        flash('Für diese Ausleihe ist kein neuer Antrag möglich.','warning')
    elif loan['renewal_requested_at']:
        flash('Ein Verlängerungsantrag ist bereits offen.','info')
    elif reason:
        flash('Verlängerung derzeit nicht möglich: '+reason,'warning')
    else:
        with db:
            db.execute("UPDATE loans SET renewal_requested_at=datetime('now') WHERE id=?",(loan_id,))
        flash('Verlängerung beantragt. Die Bibliothek prüft deinen Antrag.','success')
    return redirect(url_for('my_loans'))


# ---------------------------------------------------------------------------
# Admin: dashboard
# ---------------------------------------------------------------------------

@app.route('/admin/')
@login_required
@admin_required
def admin_dashboard():
    db = get_db()
    stats = db.execute("""
        SELECT
            (SELECT COUNT(*) FROM books)                                        AS total_books,
            (SELECT COUNT(*) FROM loans WHERE status = 'borrowed')              AS active_loans,
            (SELECT COUNT(*) FROM loans WHERE status = 'reserved')              AS pending_reservations,
            (SELECT COUNT(*) FROM loans
             WHERE status = 'borrowed' AND due_date < date('now'))              AS overdue,
            (SELECT COUNT(*) FROM users WHERE is_active = 1 AND role = 'user') AS active_users
    """).fetchone()
    recent_loans = db.execute("""
        SELECT l.*, b.title, u.full_name
        FROM loans l
        JOIN books b ON l.book_id = b.id
        JOIN users u ON l.user_id = u.id
        WHERE l.status IN ('reserved','borrowed')
        ORDER BY l.reserved_at DESC
        LIMIT 10
    """).fetchall()
    return render_template('admin/dashboard.html', stats=stats, recent_loans=recent_loans)

# ---------------------------------------------------------------------------
# Admin: books
# ---------------------------------------------------------------------------

@app.route('/admin/books')
@login_required
@admin_required
def admin_books():
    db = get_db()
    books = db.execute("""
        SELECT b.*,
               (b.total_copies -
                COUNT(CASE WHEN l.status IN ('reserved','borrowed') THEN 1 END)
               ) AS available
        FROM books b
        LEFT JOIN loans l ON l.book_id = b.id
        GROUP BY b.id
        ORDER BY b.title
    """).fetchall()
    return render_template('admin/books.html', books=books)


@app.route('/admin/books/add', methods=['POST'])
@login_required
@admin_required
def admin_add_book():
    db = get_db()
    title = request.form.get('title', '').strip()
    author = request.form.get('author', '').strip()
    isbn = request.form.get('isbn', '').strip()
    category = request.form.get('category', '').strip()
    description = request.form.get('description', '').strip()
    try:
        total_copies = max(1, int(request.form.get('total_copies', 1)))
    except ValueError:
        total_copies = 1
    if not title or not author:
        flash_msg('flash_book_missing_fields', 'danger')
        return redirect(url_for('admin_books'))
    with db:
        cur = db.execute(
            "INSERT INTO books (title, author, isbn, category, description, total_copies) VALUES (?,?,?,?,?,?)",
            (title, author, isbn or None, category or None, description or None, total_copies)
        )
        sync_book_copies(db, cur.lastrowid, total_copies)
    flash_msg('flash_book_added', 'success', title=title)
    return redirect(url_for('admin_books'))


@app.route('/admin/books/<int:book_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_book(book_id):
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        abort(404)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        author = request.form.get('author', '').strip()
        isbn = request.form.get('isbn', '').strip()
        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()
        try:
            total_copies = max(1, int(request.form.get('total_copies', 1)))
        except ValueError:
            total_copies = 1
        if not title or not author:
            flash_msg('flash_book_missing_fields', 'danger')
        else:
            try:
                with db:
                    sync_book_copies(db, book_id, total_copies)
                    db.execute("""
                        UPDATE books SET title=?, author=?, isbn=?, category=?, description=?
                        WHERE id=?
                    """, (title, author, isbn or None, category or None, description or None, book_id))
                flash_msg('flash_book_updated', 'success')
                return redirect(url_for('admin_books'))
            except ValueError as exc:
                flash(str(exc), 'danger')
    return render_template('admin/book_edit.html', book=book)


@app.route('/admin/books/<int:book_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_book(book_id):
    db = get_db()
    active = db.execute(
        "SELECT COUNT(*) FROM loans WHERE book_id = ? AND status IN ('reserved','borrowed')",
        (book_id,)
    ).fetchone()[0]
    if active > 0:
        flash_msg('flash_book_cannot_delete', 'danger')
        return redirect(url_for('admin_books'))
    with db:
        db.execute("DELETE FROM book_tags WHERE book_id = ?", (book_id,))
        db.execute("DELETE FROM book_genres WHERE book_id = ?", (book_id,))
        db.execute("DELETE FROM loans WHERE book_id = ?", (book_id,))
        db.execute("DELETE FROM book_copies WHERE book_id = ?", (book_id,))
        db.execute("DELETE FROM books WHERE id = ?", (book_id,))
    flash_msg('flash_book_deleted', 'success')
    return redirect(url_for('admin_books'))

# ---------------------------------------------------------------------------
# Admin: loans
# ---------------------------------------------------------------------------

@app.route('/admin/statistics')
@login_required
@admin_required
def admin_statistics():
    db = get_db()
    totals = db.execute("""
        SELECT
          (SELECT COUNT(*) FROM books) AS titles,
          (SELECT COUNT(*) FROM book_copies WHERE is_active=1) AS copies,
          (SELECT COUNT(*) FROM users WHERE is_active=1) AS members,
          (SELECT COUNT(*) FROM loans WHERE status='borrowed') AS borrowed,
          (SELECT COUNT(*) FROM loans WHERE status='reserved') AS reserved,
          (SELECT COUNT(*) FROM loans WHERE status='borrowed'
              AND due_date<date('now')) AS overdue,
          (SELECT COUNT(*) FROM loan_waitlist) AS waiting,
          (SELECT COUNT(*) FROM loans WHERE status='returned') AS returned
    """).fetchone()
    popular = db.execute("""
        SELECT b.title, COUNT(l.id) AS total FROM books b
        JOIN loans l ON l.book_id=b.id
        GROUP BY b.id ORDER BY total DESC,b.title LIMIT 10
    """).fetchall()
    monthly = db.execute("""
        SELECT substr(borrowed_at,1,7) AS month,COUNT(*) AS total
        FROM loans WHERE borrowed_at IS NOT NULL
        GROUP BY substr(borrowed_at,1,7) ORDER BY month DESC LIMIT 12
    """).fetchall()
    return render_template('admin/statistics.html',totals=totals,
                           popular=popular,monthly=monthly)


@app.route('/admin/loans')
@librarian_required
def admin_loans():
    db = get_db()
    expire_reservations(db)
    status_filter = request.args.get('status', '')
    search = request.args.get('q', '').strip()
    where = []
    params = []
    if status_filter in ('reserved','borrowed','returned'):
        where.append('l.status=?'); params.append(status_filter)
    if search:
        where.append("(b.title LIKE ? OR u.full_name LIKE ? OR u.username LIKE ? OR c.inventory_code LIKE ? OR c.barcode LIKE ?)")
        params.extend([f"%{search}%"]*5)
    sql = """
        SELECT l.*, b.title, b.author, u.full_name, u.username, u.email,
               c.inventory_code, c.barcode
        FROM loans l
        JOIN books b ON l.book_id=b.id
        JOIN users u ON l.user_id=u.id
        LEFT JOIN book_copies c ON c.id=l.copy_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE l.status WHEN 'reserved' THEN 0 WHEN 'borrowed' THEN 1 ELSE 2 END, l.reserved_at DESC LIMIT 200"
    loans = db.execute(sql, params).fetchall()

    stats = db.execute("""
        SELECT
          (SELECT COUNT(*) FROM loans WHERE status='reserved') AS reserved,
          (SELECT COUNT(*) FROM loans WHERE status='borrowed' AND renewal_requested_at IS NOT NULL) AS renewals,
          (SELECT COUNT(*) FROM loans WHERE status='borrowed') AS borrowed,
          (SELECT COUNT(*) FROM loans WHERE status='borrowed' AND due_date<date('now')) AS overdue
    """).fetchone()
    reservations = db.execute("""
        SELECT l.*,b.title,u.full_name,u.username,u.email
        FROM loans l JOIN books b ON b.id=l.book_id JOIN users u ON u.id=l.user_id
        WHERE l.status='reserved' ORDER BY l.reserved_at LIMIT 12
    """).fetchall()
    renewal_requests = db.execute("""
        SELECT l.*,b.title,u.full_name,c.inventory_code
        FROM loans l JOIN books b ON b.id=l.book_id JOIN users u ON u.id=l.user_id
        LEFT JOIN book_copies c ON c.id=l.copy_id
        WHERE l.status='borrowed' AND l.renewal_requested_at IS NOT NULL
        ORDER BY l.renewal_requested_at LIMIT 12
    """).fetchall()
    reminders = db.execute("""
        SELECT l.id,b.title,u.full_name,u.email,l.due_date,c.inventory_code
        FROM loans l JOIN books b ON b.id=l.book_id JOIN users u ON u.id=l.user_id
        LEFT JOIN book_copies c ON c.id=l.copy_id
        WHERE l.status='borrowed' AND l.due_date IS NOT NULL
          AND l.due_date <= date('now','+3 days')
        ORDER BY l.due_date,l.id
    """).fetchall()
    members = db.execute("""
        SELECT id,username,full_name FROM users
        WHERE is_active=1 AND role='user' ORDER BY full_name
    """).fetchall()
    available_copies = db.execute("""
        SELECT c.id,c.inventory_code,c.barcode,b.title
        FROM book_copies c JOIN books b ON b.id=c.book_id
        WHERE c.is_active=1
          AND NOT EXISTS (
            SELECT 1 FROM loans l WHERE l.copy_id=c.id AND l.status IN ('reserved','borrowed')
          )
        ORDER BY b.title,c.copy_number
    """).fetchall()
    waitlist_ready=db.execute("""
        SELECT w.book_id,b.title,u.full_name,u.username,w.requested_at,
               (SELECT COUNT(*) FROM loan_waitlist w2 WHERE w2.book_id=w.book_id) AS waiting
        FROM loan_waitlist w JOIN books b ON b.id=w.book_id JOIN users u ON u.id=w.user_id
        WHERE w.id=(SELECT w2.id FROM loan_waitlist w2 WHERE w2.book_id=w.book_id ORDER BY w2.requested_at,w2.id LIMIT 1)
          AND (SELECT COUNT(*) FROM book_copies c WHERE c.book_id=w.book_id AND c.is_active=1)
              > (SELECT COUNT(*) FROM loans l WHERE l.book_id=w.book_id AND l.status IN ('reserved','borrowed'))
        ORDER BY w.requested_at LIMIT 20
    """).fetchall()
    policy=db.execute("SELECT * FROM loan_policy WHERE id=1").fetchone()
    audit=db.execute("""SELECT a.*,b.title,u.full_name AS member_name,s.full_name AS staff_name
        FROM circulation_audit a
        LEFT JOIN books b ON b.id=a.book_id LEFT JOIN users u ON u.id=a.user_id
        LEFT JOIN users s ON s.id=a.staff_user_id
        ORDER BY a.created_at DESC,a.id DESC LIMIT 30""").fetchall()
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return render_template('admin/loans.html', loans=loans, status_filter=status_filter,
                           q=search, now=now, reminders=reminders, stats=stats,
                           reservations=reservations, renewal_requests=renewal_requests,
                           members=members, available_copies=available_copies,
                           waitlist_ready=waitlist_ready,policy=policy,audit=audit)

@app.route('/admin/loans/<int:loan_id>/confirm', methods=['POST'])
@librarian_required
def admin_confirm_loan(loan_id):
    db = get_db()
    due_date = request.form.get('due_date') or None
    if due_date:
        try:
            if datetime.strptime(due_date, '%Y-%m-%d').date() < datetime.now(timezone.utc).date():
                raise ValueError()
        except ValueError:
            flash('Bitte ein gültiges Rückgabedatum wählen (heute oder später).', 'danger')
            return redirect(url_for('admin_loans', status='reserved'))
    else:
        days = db.execute("SELECT loan_days FROM loan_policy WHERE id=1").fetchone()[0]
        due_date = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
    try:
        db.execute('BEGIN IMMEDIATE')
        loan = db.execute("SELECT book_id,copy_id FROM loans WHERE id=? AND status='reserved'", (loan_id,)).fetchone()
        if not loan:
            raise ValueError('Reservierung nicht mehr vorhanden')
        # Only library staff may assign the physical copy during the handover.
        copy_id = loan['copy_id']
        if copy_id is None:
            copy = db.execute("""
                SELECT id FROM book_copies
                WHERE book_id=? AND is_active=1
                  AND NOT EXISTS (SELECT 1 FROM loans l WHERE l.copy_id=book_copies.id
                    AND l.id<>? AND l.status IN ('reserved','borrowed'))
                ORDER BY copy_number LIMIT 1
            """, (loan['book_id'], loan_id)).fetchone()
            if not copy:
                raise ValueError('Kein freies Exemplar für die Ausgabe gefunden')
            copy_id = copy['id']
        changed = db.execute("""
            UPDATE loans SET status='borrowed', copy_id=?, borrowed_at=datetime('now'),
                due_date=? WHERE id=? AND status='reserved'
        """, (copy_id, due_date, loan_id))
        if changed.rowcount != 1:
            raise ValueError('Reservierung konnte nicht ausgegeben werden')
        full=db.execute("SELECT user_id FROM loans WHERE id=?",(loan_id,)).fetchone()
        audit_circulation(db,'checkout',loan_id,loan['book_id'],full['user_id'],f'Fällig {due_date}')
        db.commit()
        flash_msg('flash_loan_confirmed', 'success')
    except (ValueError, sqlite3.IntegrityError) as error:
        db.rollback()
        flash(str(error), 'danger')
    return redirect(url_for('admin_loans', status='reserved') + '#reservierungen')


@app.route('/admin/loans/<int:loan_id>/cancel', methods=['POST'])
@librarian_required
def admin_cancel_loan(loan_id):
    db = get_db()
    loan=db.execute("SELECT book_id,user_id FROM loans WHERE id=? AND status='reserved'",(loan_id,)).fetchone()
    if loan:
        with db:
            db.execute("UPDATE loans SET status='cancelled' WHERE id=?",(loan_id,))
            audit_circulation(db,'reservation_cancelled',loan_id,loan['book_id'],loan['user_id'])
    flash_msg('flash_loan_cancelled','success')
    return redirect(url_for('admin_loans',status='reserved') + '#reservierungen')


@app.route('/admin/waitlist/<int:book_id>/promote',methods=['POST'])
@librarian_required
def promote_waitlist(book_id):
    db=get_db()
    expire_reservations(db)
    first=db.execute("""SELECT w.user_id,u.full_name FROM loan_waitlist w
        JOIN users u ON u.id=w.user_id WHERE w.book_id=?
        ORDER BY w.requested_at,w.id LIMIT 1""",(book_id,)).fetchone()
    if not first:
        flash('Keine Person auf der Warteliste.','warning')
    elif get_available_copies(db,book_id)<=0:
        flash('Aktuell ist kein Exemplar frei.','warning')
    else:
        try:
            create_reservation(db,book_id,first['user_id'])
            loan=db.execute("""SELECT id,reservation_expires_at FROM loans
                WHERE book_id=? AND user_id=? AND status='reserved'
                ORDER BY id DESC LIMIT 1""",(book_id,first['user_id'])).fetchone()
            with db:
                audit_circulation(db,'waitlist_promoted',loan['id'],book_id,first['user_id'],
                                  'Warteliste in Reservierung umgewandelt')
            flash(f"{first['full_name']} wurde reserviert.",'success')
        except ValueError as exc:
            flash('Reservierung konnte nicht erstellt werden: '+str(exc),'danger')
    return redirect(url_for('admin_loans')+'#warteliste')


# ---------------------------------------------------------------------------
# Admin: users
# ---------------------------------------------------------------------------

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    db = get_db()
    users = db.execute("""
        SELECT u.*,
               COUNT(CASE WHEN l.status IN ('reserved','borrowed') THEN 1 END) AS active_loans
        FROM users u
        LEFT JOIN loans l ON l.user_id = u.id
        GROUP BY u.id
        ORDER BY u.full_name
    """).fetchall()
    return render_template('admin/users.html', users=users)


@app.route('/admin/users/add', methods=['POST'])
@login_required
@admin_required
def admin_add_user():
    db = get_db()
    username = request.form.get('username', '').strip()
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip()
    role = request.form.get('role', 'user')
    if not username or not full_name:
        flash_msg('flash_user_missing_fields', 'danger')
        return redirect(url_for('admin_users'))
    if role not in ('admin', 'user', 'librarian'):
        role = 'user'
    initial_pw = generate_initial_password()
    try:
        db.execute(
            """INSERT INTO users
               (username, password_hash, full_name, email, role, must_change_password)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (username, generate_password_hash(initial_pw), full_name, email or None, role)
        )
        db.commit()
        security_log.info("User created | new_user=%s by admin=%s", username, session.get('username'))
        session['show_initial_pw'] = {'username': username, 'password': initial_pw}
        flash_msg('flash_user_added', 'success', username=username)
    except sqlite3.IntegrityError:
        flash_msg('flash_username_taken', 'danger', username=username)
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
@admin_required
def admin_reset_password(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        abort(404)
    new_pw = generate_initial_password()
    db.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
        (generate_password_hash(new_pw), user_id)
    )
    db.commit()
    security_log.info("Password reset | user=%s by admin=%s", user['username'], session.get('username'))
    session['show_initial_pw'] = {'username': user['username'], 'password': new_pw}
    flash_msg('flash_pw_reset', 'success', username=user['username'])
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_user(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        abort(404)
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip()
        role = request.form.get('role', 'user')
        is_active = 1 if request.form.get('is_active') else 0
        if role not in ('admin', 'user', 'librarian'):
            role = 'user'
        if user_id == session.get('user_id') and (role != 'admin' or not is_active):
            flash('Du kannst deine eigene Administratorrolle nicht entfernen oder dich selbst sperren.', 'danger')
            return redirect(url_for('admin_users'))
        if user['role'] == 'admin' and (role != 'admin' or not is_active):
            count = db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1").fetchone()[0]
            if count <= 1:
                flash('Der letzte aktive Administrator darf nicht gesperrt oder herabgestuft werden.', 'danger')
                return redirect(url_for('admin_users'))
        db.execute("""
            UPDATE users SET full_name=?, email=?, role=?, is_active=?
            WHERE id=?
        """, (full_name, email or None, role, is_active, user_id))
        db.commit()
        security_log.info("User updated | user=%s by admin=%s", user['username'], session.get('username'))
        flash_msg('flash_user_updated', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/user_edit.html', user=user)

# ---------------------------------------------------------------------------
# Admin: system settings
# ---------------------------------------------------------------------------

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_settings():
    db = get_db()
    if request.method == 'POST':
        club_name = request.form.get('club_name', '').strip() or 'Mein Verein'
        primary_color = request.form.get('primary_color', '#0d6efd').strip()
        # Validate hex color
        if not re.match(r'^#[0-9a-fA-F]{6}$', primary_color):
            primary_color = '#0d6efd'

        logo_filename = get_settings(db)['logo_filename'] if get_settings(db) else None

        # Handle logo upload
        if 'logo' in request.files:
            file = request.files['logo']
            if file and file.filename:
                ext = file.filename.rsplit('.', 1)[-1].lower()
                if ext not in ALLOWED_LOGO_EXTENSIONS:
                    flash_msg('flash_logo_invalid', 'danger')
                    return redirect(url_for('admin_settings'))
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                safe_name = f"logo_{secrets.token_hex(8)}.{ext}"
                file.save(os.path.join(UPLOAD_DIR, safe_name))
                # Remove old logo
                if logo_filename:
                    try:
                        os.remove(os.path.join(UPLOAD_DIR, logo_filename))
                    except OSError:
                        pass
                logo_filename = safe_name

        # Handle logo deletion
        if request.form.get('delete_logo') and logo_filename:
            try:
                os.remove(os.path.join(UPLOAD_DIR, logo_filename))
            except OSError:
                pass
            logo_filename = None

        db.execute("""
            INSERT INTO settings (id, club_name, primary_color, logo_filename)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                club_name=excluded.club_name,
                primary_color=excluded.primary_color,
                logo_filename=excluded.logo_filename
        """, (club_name, primary_color, logo_filename))
        db.commit()
        flash_msg('flash_settings_saved', 'success')
        return redirect(url_for('admin_settings'))

    settings = get_settings(db)
    return render_template('admin/settings.html', settings=settings)


# ---------------------------------------------------------------------------
# Mobile REST API  (/api/*)
# All routes require header: X-LeihGut-Client: ios
# Authentication is session-cookie based (URLSession handles cookies).
# ---------------------------------------------------------------------------

def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify(error='Unauthorized'), 401
        return f(*args, **kwargs)
    return decorated


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if _is_rate_limited(username):
        remaining = _LOGIN_LOCKOUT_SECONDS // 60
        security_log.warning("API login blocked (rate limit) | user=%s ip=%s", username, request.remote_addr)
        return jsonify(error=f'Too many attempts. Try again in {remaining} minutes.'), 429

    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
    ).fetchone()

    if user and check_password_hash(user['password_hash'], password):
        _clear_attempts(username)
        session.clear()
        session['user_id'] = user['id']
        session['role'] = user['role']
        session['must_change_password'] = bool(user['must_change_password'])
        session.permanent = True
        return jsonify(
            id=user['id'],
            username=user['username'],
            full_name=user['full_name'],
            email=user['email'] or '',
            role=user['role'],
            must_change_password=bool(user['must_change_password']),
        )
    else:
        if username:
            _record_failed_attempt(username)
        return jsonify(error='Invalid username or password.'), 401


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify(ok=True)


@app.route('/api/auth/me')
@api_login_required
def api_me():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    if not user:
        return jsonify(error='Not found'), 404
    return jsonify(
        id=user['id'],
        username=user['username'],
        full_name=user['full_name'],
        email=user['email'] or '',
        role=user['role'],
        must_change_password=bool(user['must_change_password']),
    )


@app.route('/api/auth/change-password', methods=['POST'])
@api_login_required
def api_change_password():
    data = request.get_json(silent=True) or {}
    current_pw = data.get('current_password', '')
    new_pw = data.get('new_password', '')

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    if not check_password_hash(user['password_hash'], current_pw):
        return jsonify(error='Current password is incorrect.'), 400

    errors = validate_password(new_pw)
    if errors:
        return jsonify(error=' '.join(errors)), 400

    db.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
        (generate_password_hash(new_pw), session['user_id'])
    )
    db.commit()
    session['must_change_password'] = False
    return jsonify(ok=True)


@app.route('/api/catalog')
@api_login_required
def api_catalog():
    q = request.args.get('q', '').strip()
    db = get_db()
    if q:
        like = f'%{q}%'
        rows = db.execute("""
            SELECT b.*,
                   b.total_copies - (
                       SELECT COUNT(*) FROM loans
                       WHERE book_id = b.id AND status IN ('reserved','borrowed')
                   ) AS available
            FROM books b
            WHERE b.title LIKE ? OR b.author LIKE ? OR b.category LIKE ?
            ORDER BY b.title
        """, (like, like, like)).fetchall()
    else:
        rows = db.execute("""
            SELECT b.*,
                   b.total_copies - (
                       SELECT COUNT(*) FROM loans
                       WHERE book_id = b.id AND status IN ('reserved','borrowed')
                   ) AS available
            FROM books b
            ORDER BY b.title
        """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/books/<int:book_id>')
@api_login_required
def api_book_detail(book_id):
    db = get_db()
    book = db.execute("""
        SELECT b.*,
               b.total_copies - (
                   SELECT COUNT(*) FROM loans
                   WHERE book_id = b.id AND status IN ('reserved','borrowed')
               ) AS available
        FROM books b WHERE b.id = ?
    """, (book_id,)).fetchone()
    if not book:
        return jsonify(error='Not found'), 404

    user_loan = db.execute(
        "SELECT * FROM loans WHERE book_id = ? AND user_id = ? AND status IN ('reserved','borrowed')",
        (book_id, session['user_id'])
    ).fetchone()

    result = dict(book)
    result['user_loan'] = dict(user_loan) if user_loan else None
    return jsonify(result)


@app.route('/api/books/<int:book_id>/reserve', methods=['POST'])
@api_login_required
def api_reserve(book_id):
    if session.get('must_change_password'):
        return jsonify(error='You must change your password first.'), 403
    db = get_db()
    try:
        create_reservation(db, book_id, session['user_id'])
    except ValueError as exc:
        if str(exc) == 'already_reserved':
            return jsonify(error='You already have an active loan for this book.'), 409
        return jsonify(error='No copies available.'), 409
    return jsonify(ok=True), 201


@app.route('/api/my-loans')
@api_login_required
def api_my_loans():
    db = get_db()
    rows = db.execute("""
        SELECT l.*, b.title, b.author
        FROM loans l
        JOIN books b ON l.book_id = b.id
        WHERE l.user_id = ?
        ORDER BY l.reserved_at DESC
    """, (session['user_id'],)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/loans/<int:loan_id>/cancel', methods=['POST'])
@api_login_required
def api_cancel_loan(loan_id):
    db = get_db()
    loan = db.execute(
        "SELECT * FROM loans WHERE id = ? AND user_id = ? AND status = 'reserved'",
        (loan_id, session['user_id'])
    ).fetchone()
    if not loan:
        return jsonify(error='Not found or cannot be cancelled.'), 404
    db.execute("DELETE FROM loans WHERE id = ?", (loan_id,))
    db.commit()
    return jsonify(ok=True)


@app.route('/api/settings')
def api_settings():
    db = get_db()
    s = get_settings(db)
    return jsonify(
        club_name=s['club_name'] if s else 'LeihGut',
        primary_color=s['primary_color'] if s else '#0d6efd',
        logo_url=url_for('serve_upload', filename=s['logo_filename'], _external=True)
                 if s and s['logo_filename'] else None,
    )


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(_e):
    return render_template('error.html', code=403, message='Kein Zugriff.'), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template('error.html', code=404, message='Seite nicht gefunden.'), 404

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
