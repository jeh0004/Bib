
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

def resolve_member(conn,value):
    value=(value or '').strip()
    if not value: raise ValueError('Bitte ein Mitglied auswählen')
    if value.isdigit():
        row=conn.execute("SELECT id FROM users WHERE id=? AND is_active=1 AND role='user'",(int(value),)).fetchone()
    else:
        username=value.split(' – ',1)[0].strip()
        row=conn.execute("SELECT id FROM users WHERE is_active=1 AND role='user' AND (username=? OR full_name=?)",(username,value)).fetchone()
    if not row: raise ValueError('Mitglied nicht gefunden')
    return row['id']

def resolve_copy(conn,value):
    value=(value or '').strip()
    if not value: raise ValueError('Bitte ein Exemplar auswählen oder scannen')
    if value.isdigit():
        row=conn.execute("SELECT id FROM book_copies WHERE id=? AND is_active=1",(int(value),)).fetchone()
    else:
        code=value.split(' – ',1)[0].strip()
        row=conn.execute("SELECT id FROM book_copies WHERE is_active=1 AND (inventory_code=? OR barcode=?)",(code,code)).fetchone()
    if not row: raise ValueError('Exemplar nicht gefunden')
    return row['id']
def checkout(conn,copy_id,user_id):
    conn.execute('BEGIN IMMEDIATE')
    try:
        c=conn.execute('SELECT book_id,is_active FROM book_copies WHERE id=?',(copy_id,)).fetchone()
        u=conn.execute('SELECT is_active,role FROM users WHERE id=?',(user_id,)).fetchone()
        if not c or not c['is_active'] or not u or not u['is_active'] or u['role']!='user':
            raise ValueError('Exemplar oder Mitglied ungültig')
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
        cur=conn.execute("INSERT INTO loans(book_id,copy_id,user_id,status,borrowed_at,due_date) VALUES(?,?,?,'borrowed',datetime('now'),?)",(book_id,copy_id,user_id,due))
        if first: conn.execute("DELETE FROM loan_waitlist WHERE book_id=? AND user_id=?",(book_id,user_id))
        conn.execute("""INSERT INTO circulation_audit(loan_id,book_id,user_id,staff_user_id,action,details)
                        VALUES(?,?,?,?,?,?)""",(cur.lastrowid,book_id,user_id,session.get('user_id'),'checkout',f'Fällig {due}'))
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
        conn.execute("""INSERT INTO circulation_audit(loan_id,book_id,user_id,staff_user_id,action,details)
                        VALUES(?,?,?,?,?,?)""",(loan_id,l['book_id'],l['user_id'],session.get('user_id'),'renewal_approved',f'Fällig {due}'))
        conn.commit();return due
    except Exception: conn.rollback();raise
@bp.get('/')
def index():
    db()
    return redirect(url_for('admin_loans'))

@bp.post('/settings')
def settings():
    admin_only()
    conn=db()
    try:
        days=int(request.form['days']);max_renewals=int(request.form['max'])
        reservation_days=int(request.form.get('reservation_days',7))
        if not 1<=days<=365 or not 0<=max_renewals<=20 or not 1<=reservation_days<=60: raise ValueError()
        with conn: conn.execute('UPDATE loan_policy SET loan_days=?,max_renewals=?,reservation_days=? WHERE id=1',
                                (days,max_renewals,reservation_days))
        flash('Gespeichert.','success')
    except (ValueError,KeyError): flash('Ungültige Regeln.','danger')
    return redirect(url_for('admin_loans')+'#leihregeln')
@bp.post('/borrow')
def borrow():
    conn=db()
    try:
        copy_value=request.form.get('copy_lookup') or request.form.get('copy_id')
        member_value=request.form.get('member_lookup') or request.form.get('user_id')
        due=checkout(conn,resolve_copy(conn,copy_value),resolve_member(conn,member_value))
        flash('Ausgabe verbucht. Rückgabe bis '+date.fromisoformat(due).strftime('%d.%m.%Y')+'.','success')
    except (ValueError,KeyError,sqlite3.IntegrityError) as e:
        flash(str(e),'danger')
    return redirect(url_for('admin_loans')+'#direkte-ausgabe')
@bp.post('/<int:loan_id>/action')
def action(loan_id):
    conn=db()
    try:
        if request.form.get('action')=='renew': flash('Fällig am '+renew(conn,loan_id),'success')
        elif request.form.get('action')=='decline':
            with conn:
                c=conn.execute("UPDATE loans SET renewal_requested_at=NULL WHERE id=? AND status='borrowed' AND renewal_requested_at IS NOT NULL",(loan_id,))
                if not c.rowcount: raise ValueError('Kein Antrag vorhanden')
                row=conn.execute("SELECT book_id,user_id FROM loans WHERE id=?",(loan_id,)).fetchone()
                conn.execute("""INSERT INTO circulation_audit(loan_id,book_id,user_id,staff_user_id,action)
                                VALUES(?,?,?,?,?)""",(loan_id,row['book_id'],row['user_id'],session.get('user_id'),'renewal_declined'))
            flash('Verlängerungsantrag abgelehnt.','info')
        elif request.form.get('action')=='return':
            with conn:
                row=conn.execute("SELECT book_id,user_id FROM loans WHERE id=? AND status='borrowed'",(loan_id,)).fetchone()
                if not row: raise ValueError('Keine aktive Ausleihe')
                c=conn.execute("UPDATE loans SET status='returned',returned_at=datetime('now'),renewal_requested_at=NULL WHERE id=? AND status='borrowed'",(loan_id,))
                if c.rowcount!=1: raise ValueError('Keine aktive Ausleihe')
                conn.execute("""INSERT INTO circulation_audit(loan_id,book_id,user_id,staff_user_id,action)
                                VALUES(?,?,?,?,?)""",(loan_id,row['book_id'],row['user_id'],session.get('user_id'),'return'))
            flash('Zurückgegeben.','success')
        else: abort(400)
    except ValueError as e: flash(str(e),'danger')
    anchor='#verlaengerungen' if request.form.get('action') in ('renew','decline') else '#ruecknahme'
    return redirect(url_for('admin_loans')+anchor)

@bp.post('/quick-return')
def quick_return():
    conn=db()
    code=(request.form.get('return_code') or '').strip()
    if not code:
        flash('Bitte Inventarnummer oder Barcode eingeben.','warning')
        return redirect(url_for('admin_loans'))
    row=conn.execute("""
        SELECT l.id,b.title,u.full_name,c.inventory_code
        FROM loans l JOIN books b ON b.id=l.book_id JOIN users u ON u.id=l.user_id
        JOIN book_copies c ON c.id=l.copy_id
        WHERE l.status='borrowed' AND (c.inventory_code=? OR c.barcode=?)
    """,(code,code)).fetchone()
    if not row:
        flash('Keine aktive Ausleihe zu dieser Inventarnummer oder diesem Barcode gefunden.','danger')
        return redirect(url_for('admin_loans'))
    with conn:
        conn.execute("""UPDATE loans SET status='returned',returned_at=datetime('now'),
                     renewal_requested_at=NULL WHERE id=? AND status='borrowed'""",(row['id'],))
        detail=conn.execute("SELECT book_id,user_id FROM loans WHERE id=?",(row['id'],)).fetchone()
        conn.execute("""INSERT INTO circulation_audit(loan_id,book_id,user_id,staff_user_id,action)
                        VALUES(?,?,?,?,?)""",(row['id'],detail['book_id'],detail['user_id'],session.get('user_id'),'return'))
    flash(f"Rücknahme verbucht: {row['title']} – {row['full_name']}.",'success')
    return redirect(url_for('admin_loans')+'#ruecknahme')
