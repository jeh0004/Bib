import sqlite3
import os
import re
import random
import secrets
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
    lang = session.get('lang', 'en')
    t = STRINGS.get(lang, STRINGS['en'])
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


def flash_msg(key, category='info', **kwargs):
    """Flash a translated message looked up from STRINGS by key."""
    lang = session.get('lang', 'en')
    strings = STRINGS.get(lang, STRINGS['en'])
    msg = strings.get(key, STRINGS['en'].get(key, key))
    if kwargs:
        msg = msg.format(**kwargs)
    flash(msg, category)


@app.route('/set-language/<lang>')
def set_language(lang):
    if lang in STRINGS:
        session['lang'] = lang
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
        _add_column_if_missing(db, 'users', 'must_change_password', 'INTEGER NOT NULL DEFAULT 0')
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
    lang = session.get('lang', 'en') if session else 'en'
    t = STRINGS.get(lang, STRINGS['en'])
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
        "img-src 'self' data:; "
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
        if session.get('must_change_password') and request.endpoint != 'change_password':
            flash_msg('flash_must_change_pw', 'warning')
            return redirect(url_for('change_password'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def get_available_copies(db, book_id):
    book = db.execute("SELECT total_copies FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        return 0
    active = db.execute(
        "SELECT COUNT(*) FROM loans WHERE book_id = ? AND status IN ('reserved', 'borrowed')",
        (book_id,)
    ).fetchone()[0]
    return book['total_copies'] - active

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
            errors.append(STRINGS.get(session.get('lang', 'en'), STRINGS['en'])['flash_pw_mismatch'])
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
            errors.append(STRINGS.get(session.get('lang', 'en'), STRINGS['en'])['flash_pw_mismatch'])
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
    return redirect(url_for('catalog'))


@app.route('/catalog')
@login_required
def catalog():
    db = get_db()
    q = request.args.get('q', '').strip()
    term = f"%{q}%"
    books = db.execute("""
        SELECT b.*,
               (b.total_copies -
                COUNT(CASE WHEN l.status IN ('reserved','borrowed') THEN 1 END)
               ) AS available
        FROM books b
        LEFT JOIN loans l ON l.book_id = b.id
        WHERE b.title LIKE ? OR b.author LIKE ? OR b.category LIKE ?
        GROUP BY b.id
        ORDER BY b.title
    """, (term, term, term)).fetchall()
    return render_template('catalog.html', books=books, q=q)


@app.route('/book/<int:book_id>')
@login_required
def book_detail(book_id):
    db = get_db()
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
    return render_template('book_detail.html', book=book, available=available,
                           loans=loans, my_loan=my_loan)


@app.route('/book/<int:book_id>/reserve', methods=['POST'])
@login_required
def reserve_book(book_id):
    db = get_db()
    existing = db.execute("""
        SELECT 1 FROM loans
        WHERE book_id = ? AND user_id = ? AND status IN ('reserved','borrowed')
    """, (book_id, session['user_id'])).fetchone()
    if existing:
        flash_msg('flash_already_reserved', 'warning')
        return redirect(url_for('book_detail', book_id=book_id))
    if get_available_copies(db, book_id) <= 0:
        flash_msg('flash_not_available', 'danger')
        return redirect(url_for('book_detail', book_id=book_id))
    db.execute(
        "INSERT INTO loans (book_id, user_id, status) VALUES (?, ?, 'reserved')",
        (book_id, session['user_id'])
    )
    db.commit()
    flash_msg('flash_reserved_ok', 'success')
    return redirect(url_for('my_loans'))


@app.route('/book/<int:book_id>/return', methods=['POST'])
@login_required
def return_book(book_id):
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
    db = get_db()
    active = db.execute("""
        SELECT l.*, b.title, b.author
        FROM loans l JOIN books b ON l.book_id = b.id
        WHERE l.user_id = ? AND l.status IN ('reserved','borrowed')
        ORDER BY l.reserved_at DESC
    """, (session['user_id'],)).fetchall()
    history = db.execute("""
        SELECT l.*, b.title, b.author
        FROM loans l JOIN books b ON l.book_id = b.id
        WHERE l.user_id = ? AND l.status = 'returned'
        ORDER BY l.returned_at DESC
        LIMIT 20
    """, (session['user_id'],)).fetchall()
    return render_template('user/my_loans.html', active=active, history=history)

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
    db.execute(
        "INSERT INTO books (title, author, isbn, category, description, total_copies) VALUES (?,?,?,?,?,?)",
        (title, author, isbn or None, category or None, description or None, total_copies)
    )
    db.commit()
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
            db.execute("""
                UPDATE books SET title=?, author=?, isbn=?, category=?, description=?, total_copies=?
                WHERE id=?
            """, (title, author, isbn or None, category or None, description or None,
                  total_copies, book_id))
            db.commit()
            flash_msg('flash_book_updated', 'success')
            return redirect(url_for('admin_books'))
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
    db.execute("DELETE FROM loans WHERE book_id = ?", (book_id,))
    db.execute("DELETE FROM books WHERE id = ?", (book_id,))
    db.commit()
    flash_msg('flash_book_deleted', 'success')
    return redirect(url_for('admin_books'))

# ---------------------------------------------------------------------------
# Admin: loans
# ---------------------------------------------------------------------------

@app.route('/admin/loans')
@login_required
@admin_required
def admin_loans():
    db = get_db()
    status_filter = request.args.get('status', '')
    if status_filter in ('reserved', 'borrowed', 'returned'):
        loans = db.execute("""
            SELECT l.*, b.title, u.full_name, u.username, u.email
            FROM loans l
            JOIN books b ON l.book_id = b.id
            JOIN users u ON l.user_id = u.id
            WHERE l.status = ?
            ORDER BY l.reserved_at DESC
        """, (status_filter,)).fetchall()
    else:
        loans = db.execute("""
            SELECT l.*, b.title, u.full_name, u.username, u.email
            FROM loans l
            JOIN books b ON l.book_id = b.id
            JOIN users u ON l.user_id = u.id
            ORDER BY l.reserved_at DESC
            LIMIT 100
        """).fetchall()
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return render_template('admin/loans.html', loans=loans, status_filter=status_filter, now=now)


@app.route('/admin/loans/<int:loan_id>/confirm', methods=['POST'])
@login_required
@admin_required
def admin_confirm_loan(loan_id):
    db = get_db()
    due_date = request.form.get('due_date') or None
    db.execute("""
        UPDATE loans SET status = 'borrowed', borrowed_at = datetime('now'), due_date = ?
        WHERE id = ? AND status = 'reserved'
    """, (due_date, loan_id))
    db.commit()
    flash_msg('flash_loan_confirmed', 'success')
    return redirect(url_for('admin_loans', status='reserved'))


@app.route('/admin/loans/<int:loan_id>/cancel', methods=['POST'])
@login_required
@admin_required
def admin_cancel_loan(loan_id):
    db = get_db()
    db.execute("DELETE FROM loans WHERE id = ? AND status = 'reserved'", (loan_id,))
    db.commit()
    flash_msg('flash_loan_cancelled', 'success')
    return redirect(url_for('admin_loans', status='reserved'))

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
    if role not in ('admin', 'user'):
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
        if role not in ('admin', 'user'):
            role = 'user'
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
    if get_available_copies(db, book_id) < 1:
        return jsonify(error='No copies available.'), 409
    existing = db.execute(
        "SELECT id FROM loans WHERE user_id = ? AND book_id = ? AND status IN ('reserved','borrowed')",
        (session['user_id'], book_id)
    ).fetchone()
    if existing:
        return jsonify(error='You already have an active loan for this book.'), 409
    db.execute(
        "INSERT INTO loans (user_id, book_id, status, reserved_at) VALUES (?, ?, 'reserved', datetime('now'))",
        (session['user_id'], book_id)
    )
    db.commit()
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
