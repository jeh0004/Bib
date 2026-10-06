
from datetime import date,timedelta
import sqlite3
from flask import Blueprint,abort,flash,redirect,render_template_string,request,session,url_for
bp=Blueprint('loans_extra',__name__,url_prefix='/admin/loans-extra')
def db():
    if not session.get('user_id') or session.get('must_change_password'): abort(403)
    from app import get_db
    conn=get_db()
    user=conn.execute('SELECT role,is_active FROM users WHERE id=?',(session['user_id'],)).fetchone()
    if not user or not user['is_active'] or user['role'] not in ('admin','librarian'): abort(403)
    return conn

def admin_only():
    conn=db()
    if conn.execute('SELECT role FROM users WHERE id=?',(session['user_id'],)).fetchone()[0]!='admin': abort(403)
def checkout(conn,copy_id,user_id):
    conn.execute('BEGIN IMMEDIATE')
    try:
        c=conn.execute('SELECT book_id,is_active FROM book_copies WHERE id=?',(copy_id,)).fetchone()
        u=conn.execute('SELECT is_active FROM users WHERE id=?',(user_id,)).fetchone()
        if not c or not c['is_active'] or not u or not u['is_active']: raise ValueError('Exemplar oder Mitglied ungültig')
        book_id=c['book_id']
        first=conn.execute("SELECT user_id FROM loan_waitlist WHERE book_id=? ORDER BY requested_at,id LIMIT 1",(book_id,)).fetchone()
        if first and first['user_id']!=user_id: raise ValueError('Warteliste hat Vorrang')
        active=conn.execute("SELECT COUNT(*) FROM loans WHERE book_id=? AND status IN ('reserved','borrowed')",(book_id,)).fetchone()[0]
        copies=conn.execute('SELECT COUNT(*) FROM book_copies WHERE book_id=? AND is_active=1',(book_id,)).fetchone()[0]
        legacy=conn.execute('SELECT total_copies FROM books WHERE id=?',(book_id,)).fetchone()[0]
        if active>=min(copies,legacy): raise ValueError('Keine freie Kapazität')
        if conn.execute("SELECT 1 FROM loans WHERE copy_id=? AND status IN ('reserved','borrowed')",(copy_id,)).fetchone(): raise ValueError('Exemplar vergeben')
        assigned=conn.execute("SELECT COUNT(DISTINCT copy_id) FROM loans WHERE book_id=? AND copy_id IS NOT NULL AND status IN ('reserved','borrowed')",(book_id,)).fetchone()[0]
        if copies - assigned - (active - assigned) <= 0: raise ValueError('Alt-Ausleihen belegen den verfügbaren Bestand')
        if conn.execute("SELECT 1 FROM loans WHERE book_id=? AND user_id=? AND status IN ('reserved','borrowed')",(book_id,user_id)).fetchone(): raise ValueError('Bereits ausgeliehen/reserviert')
        days=conn.execute('SELECT loan_days FROM loan_policy WHERE id=1').fetchone()[0]
        due=(date.today()+timedelta(days=days)).isoformat()
        conn.execute("INSERT INTO loans(book_id,copy_id,user_id,status,borrowed_at,due_date) VALUES(?,?,?,'borrowed',datetime('now'),?)",(book_id,copy_id,user_id,due))
        if first: conn.execute("DELETE FROM loan_waitlist WHERE book_id=? AND user_id=?",(book_id,user_id))
        conn.commit();return due
    except Exception: conn.rollback();raise
def renew(conn,loan_id):
    conn.execute('BEGIN IMMEDIATE')
    try:
        l=conn.execute("SELECT * FROM loans WHERE id=? AND status='borrowed' AND renewal_requested_at IS NOT NULL",(loan_id,)).fetchone()
        if not l or not l['due_date']: raise ValueError('Keine aktive Ausleihe mit Frist')
        p=conn.execute('SELECT loan_days,max_renewals FROM loan_policy WHERE id=1').fetchone()
        if l['renewal_count']>=p['max_renewals']: raise ValueError('Verlängerungslimit erreicht')
        if conn.execute("SELECT 1 FROM loans WHERE book_id=? AND status='reserved' AND id<>?",(l['book_id'],loan_id)).fetchone(): raise ValueError('Reservierung vorhanden')
        if conn.execute("SELECT 1 FROM loan_waitlist WHERE book_id=?",(l['book_id'],)).fetchone(): raise ValueError('Warteliste vorhanden')
        due=(max(date.today(),date.fromisoformat(l['due_date']))+timedelta(days=p['loan_days'])).isoformat()
        conn.execute('UPDATE loans SET due_date=?,renewal_count=renewal_count+1,renewal_requested_at=NULL WHERE id=?',(due,loan_id))
        conn.commit();return due
    except Exception: conn.rollback();raise
@bp.get('/')
def index():
    conn=db()
    rows=conn.execute("""SELECT l.id,l.status,l.due_date,l.renewal_count,l.renewal_requested_at,b.title,u.full_name,c.inventory_code FROM loans l JOIN books b ON b.id=l.book_id JOIN users u ON u.id=l.user_id LEFT JOIN book_copies c ON c.id=l.copy_id WHERE l.status IN ('borrowed','reserved') ORDER BY l.due_date,l.id""").fetchall()
    users=conn.execute("SELECT id,full_name FROM users WHERE is_active=1 AND role='user' ORDER BY full_name").fetchall()
    copies=conn.execute('SELECT c.id,c.inventory_code,b.title FROM book_copies c JOIN books b ON b.id=c.book_id WHERE c.is_active=1 ORDER BY b.title,c.copy_number').fetchall()
    p=conn.execute('SELECT * FROM loan_policy WHERE id=1').fetchone()
    is_admin=conn.execute('SELECT role FROM users WHERE id=?',(session['user_id'],)).fetchone()[0]=='admin'
    return render_template_string("""{% extends 'base.html' %}
{% block title %}Bibliothek – Ausleihe{% endblock %}
{% block content %}
<a href="{{url_for('admin_loans')}}" class="text-decoration-none">← Zur Übersicht der Ausleihen</a>
<h1 class="h3 mt-3 mb-2">Exemplar ausgeben</h1>
<p class="text-muted">Für nicht reservierte Bücher ein freies Exemplar auswählen und dem Mitglied aushändigen. Bestehende Reservierungen bitte in der <a href="{{url_for('admin_loans',status='reserved')}}">Reservierungsliste</a> bestätigen.</p>
<div class="card border-0 shadow-sm mb-4"><div class="card-body">
<form method="post" action="{{url_for('loans_extra.borrow')}}">
<input type="hidden" name="csrf_token" value="{{session.csrf_token}}">
<div class="mb-3"><label class="form-label">Exemplar</label><select class="form-select" name="copy_id" required>
{% for c in copies %}<option value="{{c.id}}">{{c.title}} – {{c.inventory_code}}</option>{% endfor %}</select></div>
<div class="mb-3"><label class="form-label">Mitglied</label><select class="form-select" name="user_id" required>
{% for u in users %}<option value="{{u.id}}">{{u.full_name}}</option>{% endfor %}</select></div>
<button class="btn btn-primary">Ausgabe verbuchen</button>
</form></div></div>
{% if is_admin %}
<details class="border rounded p-3 mb-4"><summary>Leihregeln (nur Administrator)</summary>
<form method="post" class="mt-3" action="{{url_for('loans_extra.settings')}}">
<input type="hidden" name="csrf_token" value="{{session.csrf_token}}">
<label class="form-label">Leihfrist in Tagen<input class="form-control" type="number" name="days" min="1" max="365" value="{{p.loan_days}}"></label>
<label class="form-label ms-2">Max. Verlängerungen<input class="form-control" type="number" name="max" min="0" max="20" value="{{p.max_renewals}}"></label>
<button class="btn btn-outline-secondary d-block">Regeln speichern</button></form></details>
{% endif %}
<h2 class="h5">Aktive Vorgänge</h2>
{% if not rows %}<p class="text-muted">Keine aktiven Vorgänge.</p>{% endif %}
<div class="list-group">
{% for l in rows %}
<div class="list-group-item">
<strong>{{l.title}}</strong><span class="text-muted"> – {{l.full_name}}</span>
<div class="small text-muted">{{l.inventory_code or 'Exemplar noch nicht zugeordnet'}} · {{'Reserviert' if l.status=='reserved' else 'Ausgeliehen'}} · Rückgabe {{l.due_date or 'offen'}}</div>
{% if l.renewal_requested_at %}<span class="badge bg-warning text-dark">Verlängerung beantragt</span>{% endif %}
{% if l.status=='borrowed' %}
<form method="post" class="mt-2 d-flex gap-2 flex-wrap" action="{{url_for('loans_extra.action',loan_id=l.id)}}">
<input type="hidden" name="csrf_token" value="{{session.csrf_token}}">
{% if l.renewal_requested_at %}<button class="btn btn-sm btn-outline-primary" name="action" value="renew">Verlängerung genehmigen</button>
<button class="btn btn-sm btn-outline-secondary" name="action" value="decline">Ablehnen</button>{% endif %}
<button class="btn btn-sm btn-outline-success" name="action" value="return" onclick="return confirm('Rückgabe tatsächlich entgegengenommen?')">Rücknahme buchen</button></form>
{% elif l.status=='reserved' %}<a class="btn btn-sm btn-outline-primary mt-2" href="{{url_for('admin_loans',status='reserved')}}">Reservierung bearbeiten</a>{% endif %}
</div>{% endfor %}</div>
{% endblock %}""",rows=rows,users=users,copies=copies,p=p,is_admin=is_admin)
@bp.post('/settings')
def settings():
    admin_only()
    conn=db()
    try:
        days=int(request.form['days']);max_renewals=int(request.form['max'])
        if not 1<=days<=365 or not 0<=max_renewals<=20: raise ValueError()
        with conn: conn.execute('UPDATE loan_policy SET loan_days=?,max_renewals=? WHERE id=1',(days,max_renewals))
        flash('Gespeichert.','success')
    except (ValueError,KeyError): flash('Ungültige Regeln.','danger')
    return redirect(url_for('.index'))
@bp.post('/borrow')
def borrow():
    conn=db()
    try: flash('Fällig am '+checkout(conn,int(request.form['copy_id']),int(request.form['user_id'])),'success')
    except (ValueError,KeyError,sqlite3.IntegrityError) as e: flash(str(e),'danger')
    return redirect(url_for('.index'))
@bp.post('/<int:loan_id>/action')
def action(loan_id):
    conn=db()
    try:
        if request.form.get('action')=='renew': flash('Fällig am '+renew(conn,loan_id),'success')
        elif request.form.get('action')=='decline':
            with conn:
                c=conn.execute("UPDATE loans SET renewal_requested_at=NULL WHERE id=? AND status='borrowed' AND renewal_requested_at IS NOT NULL",(loan_id,))
                if not c.rowcount: raise ValueError('Kein Antrag vorhanden')
            flash('Verlängerungsantrag abgelehnt.','info')
        elif request.form.get('action')=='return':
            with conn:
                c=conn.execute("UPDATE loans SET status='returned',returned_at=datetime('now'),renewal_requested_at=NULL WHERE id=? AND status='borrowed'",(loan_id,))
                if c.rowcount!=1: raise ValueError('Keine aktive Ausleihe')
            flash('Zurückgegeben.','success')
        else: abort(400)
    except ValueError as e: flash(str(e),'danger')
    return redirect(url_for('.index'))
