
from datetime import date,timedelta
import sqlite3
from flask import Blueprint,abort,flash,redirect,render_template_string,request,session,url_for
bp=Blueprint('loans_extra',__name__,url_prefix='/admin/loans-extra')
def db():
    if not session.get('user_id') or session.get('role')!='admin' or session.get('must_change_password'): abort(403)
    from app import get_db
    return get_db()
def checkout(conn,copy_id,user_id):
    conn.execute('BEGIN IMMEDIATE')
    try:
        c=conn.execute('SELECT book_id,is_active FROM book_copies WHERE id=?',(copy_id,)).fetchone()
        u=conn.execute('SELECT is_active FROM users WHERE id=?',(user_id,)).fetchone()
        if not c or not c['is_active'] or not u or not u['is_active']: raise ValueError('Exemplar oder Mitglied ungültig')
        book_id=c['book_id']
        active=conn.execute("SELECT COUNT(*) FROM loans WHERE book_id=? AND status IN ('reserved','borrowed')",(book_id,)).fetchone()[0]
        copies=conn.execute('SELECT COUNT(*) FROM book_copies WHERE book_id=? AND is_active=1',(book_id,)).fetchone()[0]
        legacy=conn.execute('SELECT total_copies FROM books WHERE id=?',(book_id,)).fetchone()[0]
        if active>=min(copies,legacy): raise ValueError('Keine freie Kapazität')
        if conn.execute("SELECT 1 FROM loans WHERE copy_id=? AND status IN ('reserved','borrowed')",(copy_id,)).fetchone(): raise ValueError('Exemplar vergeben')
        if conn.execute("SELECT 1 FROM loans WHERE book_id=? AND user_id=? AND status IN ('reserved','borrowed')",(book_id,user_id)).fetchone(): raise ValueError('Bereits ausgeliehen/reserviert')
        days=conn.execute('SELECT loan_days FROM loan_policy WHERE id=1').fetchone()[0]
        due=(date.today()+timedelta(days=days)).isoformat()
        conn.execute("INSERT INTO loans(book_id,copy_id,user_id,status,borrowed_at,due_date) VALUES(?,?,?,'borrowed',datetime('now'),?)",(book_id,copy_id,user_id,due))
        conn.commit();return due
    except Exception: conn.rollback();raise
def renew(conn,loan_id):
    conn.execute('BEGIN IMMEDIATE')
    try:
        l=conn.execute("SELECT * FROM loans WHERE id=? AND status='borrowed'",(loan_id,)).fetchone()
        if not l or not l['due_date']: raise ValueError('Keine aktive Ausleihe mit Frist')
        p=conn.execute('SELECT loan_days,max_renewals FROM loan_policy WHERE id=1').fetchone()
        if l['renewal_count']>=p['max_renewals']: raise ValueError('Verlängerungslimit erreicht')
        if conn.execute("SELECT 1 FROM loans WHERE book_id=? AND status='reserved' AND id<>?",(l['book_id'],loan_id)).fetchone(): raise ValueError('Reservierung vorhanden')
        due=(max(date.today(),date.fromisoformat(l['due_date']))+timedelta(days=p['loan_days'])).isoformat()
        conn.execute('UPDATE loans SET due_date=?,renewal_count=renewal_count+1 WHERE id=?',(due,loan_id))
        conn.commit();return due
    except Exception: conn.rollback();raise
@bp.get('/')
def index():
    conn=db()
    rows=conn.execute("""SELECT l.id,l.status,l.due_date,l.renewal_count,b.title,u.full_name,c.inventory_code FROM loans l JOIN books b ON b.id=l.book_id JOIN users u ON u.id=l.user_id LEFT JOIN book_copies c ON c.id=l.copy_id WHERE l.status IN ('borrowed','reserved') ORDER BY l.due_date,l.id""").fetchall()
    users=conn.execute('SELECT id,full_name FROM users WHERE is_active=1 ORDER BY full_name').fetchall()
    copies=conn.execute('SELECT c.id,c.inventory_code,b.title FROM book_copies c JOIN books b ON b.id=c.book_id WHERE c.is_active=1 ORDER BY b.title,c.copy_number').fetchall()
    p=conn.execute('SELECT * FROM loan_policy WHERE id=1').fetchone()
    return render_template_string('''<!doctype html><html lang="de"><meta charset="utf-8"><title>LeihGut Ausleihe</title><a href="/admin/">Admin</a> | <a href="/admin/catalog-extra/">Katalog</a><h1>Exemplar-Ausleihe</h1>{% for cat,msg in get_flashed_messages(with_categories=true) %}<p>{{msg}}</p>{% endfor %}<form method="post" action="{{url_for('loans_extra.settings')}}"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}">Leihfrist <input type="number" name="days" min="1" max="365" value="{{p.loan_days}}">Verlängerungen <input type="number" name="max" min="0" max="20" value="{{p.max_renewals}}"><button>Regeln speichern</button></form><form method="post" action="{{url_for('loans_extra.borrow')}}"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><select name="copy_id">{% for c in copies %}<option value="{{c.id}}">{{c.title}} – {{c.inventory_code}}</option>{% endfor %}</select><select name="user_id">{% for u in users %}<option value="{{u.id}}">{{u.full_name}}</option>{% endfor %}</select><button>Ausleihen</button></form><h2>Aktive Vorgänge</h2>{% for l in rows %}<p>{{l.title}} – {{l.full_name}} – {{l.inventory_code or 'Altbestand'}} – {{l.status}} – {{l.due_date or 'ohne Frist'}} {% if l.status=='borrowed' %}<form method="post" action="{{url_for('loans_extra.action',loan_id=l.id)}}"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><button name="action" value="renew">Verlängern</button><button name="action" value="return">Rückgabe</button></form>{% endif %}</p>{% endfor %}''',rows=rows,users=users,copies=copies,p=p)
@bp.post('/settings')
def settings():
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
        elif request.form.get('action')=='return':
            with conn:
                c=conn.execute("UPDATE loans SET status='returned',returned_at=datetime('now') WHERE id=? AND status='borrowed'",(loan_id,))
                if c.rowcount!=1: raise ValueError('Keine aktive Ausleihe')
            flash('Zurückgegeben.','success')
        else: abort(400)
    except ValueError as e: flash(str(e),'danger')
    return redirect(url_for('.index'))
