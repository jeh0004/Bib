
import json, secrets, sqlite3
from urllib.request import Request, urlopen
from flask import Blueprint, abort, flash, redirect, render_template_string, request, session, url_for
bp=Blueprint('catalog_extra',__name__,url_prefix='/admin/catalog-extra')
def db():
    if not session.get('user_id') or session.get('role')!='admin' or session.get('must_change_password'): abort(403)
    from app import get_db
    return get_db()
def view(body,**kw):
    page = """{% extends 'base.html' %}{% block title %}Buchverwaltung – LeihGut{% endblock %}
    {% block content %}<div class="mb-4"><a class="text-decoration-none" href="{{url_for('admin_dashboard')}}">← Verwaltung</a>
    <span class="mx-2 text-muted">/</span><a href="{{url_for('catalog_extra.index')}}">Bücher verwalten</a>
    <span class="mx-2 text-muted">/</span><a href="{{url_for('admin_loans')}}">Ausleihen</a></div>
    {% for cat,msg in get_flashed_messages(with_categories=true) %}<div class="alert alert-{{'danger' if cat=='error' else cat}}">{{msg}}</div>{% endfor %}
    <div class="card shadow-sm border-0"><div class="card-body p-4">""" + body + """</div></div>
    {% endblock %}"""
    return render_template_string(page,**kw)
def isbn_lookup(isbn):
    """Fetch edition-specific metadata; never guess a match from title alone."""
    digits=''.join(ch for ch in isbn.upper() if ch.isdigit() or ch=='X')
    if len(digits) not in (10,13): raise ValueError('ISBN ungültig')
    def get(path):
        with urlopen(Request('https://openlibrary.org'+path,
                             headers={'User-Agent':'LeihGut/1.0 (library catalog)'}),
                     timeout=5) as response:
            return json.load(response)
    data=get('/api/books?bibkeys=ISBN:'+digits+'&jscmd=data&format=json')
    entry=data.get('ISBN:'+digits)
    if not entry: raise ValueError('Keine Informationen zu dieser ISBN gefunden')
    authors=', '.join(a.get('name','') for a in entry.get('authors',[])[:5])
    description=entry.get('excerpts') or []
    desc=description[0].get('text','') if description else ''
    if isinstance(desc,dict): desc=desc.get('value','')
    cover=entry.get('cover') or {}
    cover_url=cover.get('large') or cover.get('medium') or ''
    return dict(title=entry.get('title',''),author=authors,isbn=digits,
                description=desc,cover_url=cover_url,
                publisher=', '.join(x.get('name','') for x in entry.get('publishers',[])[:3]),
                published=entry.get('publish_date',''),
                pages=entry.get('number_of_pages'))


@bp.get('/')
def index():
    books=db().execute('SELECT b.*,COUNT(c.id) copies FROM books b LEFT JOIN book_copies c ON b.id=c.book_id GROUP BY b.id ORDER BY b.title').fetchall()
    return view('''<h1 class="h3">Bücher verwalten</h1><p class="text-muted">ISBN-Daten und Cover ergänzen, Bücher bearbeiten und Exemplare verwalten.</p><div class="d-flex flex-wrap gap-2 mb-4"><a class="btn btn-primary" href="{{url_for('catalog_extra.add')}}">Buch hinzufügen</a><a class="btn btn-outline-secondary" href="{{url_for('catalog_extra.inventory')}}">Inventarliste</a><form method="post" action="{{url_for('catalog_extra.enrich_missing')}}"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><button class="btn btn-outline-primary">Fehlende ISBN-Daten ergänzen (bis zu 10)</button></form></div><div class="list-group">{% for b in books %}<a class="list-group-item list-group-item-action d-flex align-items-center gap-3" href="{{url_for('catalog_extra.detail',book_id=b.id)}}">{% if b.cover_url %}<img src="{{b.cover_url}}" width="42" height="62" style="object-fit:contain" alt="">{% else %}<span class="fs-2">📖</span>{% endif %}<span class="flex-grow-1"><strong>{{b.title}}</strong><br><span class="text-muted small">{{b.author}} · {{b.copies}} Exemplare</span></span><span aria-hidden="true">›</span></a>{% endfor %}</div>''',books=books)
@bp.post('/enrich-missing')
def enrich_missing():
    conn=db()
    rows=conn.execute("""SELECT id,isbn,title,author,description,cover_url,publisher,published,pages
        FROM books WHERE isbn IS NOT NULL AND TRIM(isbn)!=''
        AND (cover_url IS NULL OR TRIM(cover_url)='' OR publisher IS NULL OR TRIM(publisher)=''
             OR published IS NULL OR TRIM(published)='' OR description IS NULL OR TRIM(description)='')
        ORDER BY id LIMIT 10""").fetchall()
    changed=0; failed=0
    for book in rows:
        try:
            meta=isbn_lookup(book['isbn'])
            updates={field:meta[field] for field in ('title','author','description','cover_url','publisher','published','pages')
                     if not book[field] and meta.get(field)}
            if updates:
                with conn:
                    conn.execute('UPDATE books SET '+','.join(f'{field}=?' for field in updates)+' WHERE id=?',
                                 list(updates.values())+[book['id']])
                changed+=1
        except Exception:
            failed+=1
    flash(f'{changed} Bücher ergänzt, {failed} ISBN-Abfragen fehlgeschlagen. Erneut klicken für die nächsten Bücher.','info')
    return redirect(url_for('.index'))

@bp.get('/inventory')
def inventory():
    conn=db()
    q=request.args.get('q','').strip()
    term='%'+q+'%'
    rows=conn.execute("""
        SELECT c.id,c.inventory_code,c.barcode,c.location,c.shelf,c.condition,
               c.is_active,b.title,b.author,
               CASE WHEN EXISTS (
                   SELECT 1 FROM loans l WHERE l.copy_id=c.id
                   AND l.status IN ('reserved','borrowed')
               ) THEN 'vergeben' ELSE 'frei' END AS state
        FROM book_copies c JOIN books b ON b.id=c.book_id
        WHERE b.title LIKE ? OR b.isbn LIKE ? OR c.inventory_code LIKE ?
           OR c.barcode LIKE ? OR c.location LIKE ? OR c.shelf LIKE ?
        ORDER BY b.title,c.copy_number LIMIT 500
    """,(term,)*6).fetchall()
    return view("""<h1>Inventarliste</h1><form method="get">
    <input name="q" value="{{q}}" placeholder="Titel, ISBN, Barcode, Regal">
    <button>Suchen</button></form><p>Maximal 500 Exemplare pro Ansicht.</p>
    <table><tr><th>Titel</th><th>Inventarnummer</th><th>Barcode</th>
    <th>Standort</th><th>Regal</th><th>Zustand</th><th>Status</th></tr>
    {% for c in rows %}<tr><td>{{c.title}}</td><td>{{c.inventory_code}}</td>
    <td>{{c.barcode or '–'}}</td><td>{{c.location or '–'}}</td>
    <td>{{c.shelf or '–'}}</td><td>{{c.condition}}</td>
    <td>{{'inaktiv' if not c.is_active else c.state}}</td></tr>{% endfor %}
    </table>""",rows=rows,q=q)

@bp.route('/add',methods=['GET','POST'])
def add():
    conn=db(); meta={}
    if request.method=='GET' and request.args.get('isbn'):
        try: meta=isbn_lookup(request.args['isbn'])
        except Exception: flash('ISBN konnte nicht geladen werden.','warning')
    if request.method=='POST':
        title=request.form.get('title','').strip();author=request.form.get('author','').strip()
        if title and author:
            with conn:
                cur=conn.execute('INSERT INTO books(title,author,isbn,category,description,total_copies,cover_url,publisher,published,pages) VALUES(?,?,?,?,?,0,?,?,?,?)',(title,author,request.form.get('isbn',''),request.form.get('category',''),request.form.get('description',''),request.form.get('cover_url',''),request.form.get('publisher',''),request.form.get('published',''),request.form.get('pages') or None))
            return redirect(url_for('.detail',book_id=cur.lastrowid))
        flash('Titel und Autor sind erforderlich.','danger')
    return view('<h1>Buch anlegen</h1><form method="get"><input name="isbn" placeholder="ISBN"><button>ISBN suchen</button></form><form method="post"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}">{% for field in ["title","author","isbn","category","cover_url","description","publisher","published","pages"] %}<p><label>{{field}} <input name="{{field}}" value="{{meta.get(field,"")}}"></label></p>{% endfor %}<button>Speichern</button></form>',meta=meta)
@bp.route('/<int:book_id>',methods=['GET','POST'])
def detail(book_id):
    conn=db();book=conn.execute('SELECT * FROM books WHERE id=?',(book_id,)).fetchone()
    if not book: abort(404)
    if request.method=='POST':
        action=request.form.get('action')
        try:
            meta=isbn_lookup(book['isbn']) if action=='enrich' and book['isbn'] else None
            with conn:
                if action=='add_copy':
                    number=conn.execute('SELECT COALESCE(MAX(copy_number),0)+1 FROM book_copies WHERE book_id=?',(book_id,)).fetchone()[0]
                    code=request.form.get('inventory_code','').strip() or f'LG-{book_id}-{number}'
                    conn.execute('INSERT INTO book_copies(book_id,copy_number,inventory_code,barcode,qr_token,location,shelf,condition) VALUES(?,?,?,?,?,?,?,?)',(book_id,number,code,request.form.get('barcode') or None,secrets.token_urlsafe(18),request.form.get('location'),request.form.get('shelf'),request.form.get('condition','good')))
                    conn.execute('UPDATE books SET total_copies=(SELECT COUNT(*) FROM book_copies WHERE book_id=? AND is_active=1) WHERE id=?',(book_id,book_id))
                elif action=='copy':
                    conn.execute('UPDATE book_copies SET inventory_code=?,barcode=?,location=?,shelf=?,condition=?,notes=? WHERE id=? AND book_id=?',(request.form.get('inventory_code'),request.form.get('barcode') or None,request.form.get('location'),request.form.get('shelf'),request.form.get('condition'),request.form.get('notes'),request.form.get('copy_id'),book_id))
                elif action in ('tags','genres'):
                    table,link,fk=('tags','book_tags','tag_id') if action=='tags' else ('genres','book_genres','genre_id')
                    conn.execute(f'DELETE FROM {link} WHERE book_id=?',(book_id,))
                    for name in list(dict.fromkeys(n.strip() for n in request.form.get('names','').split(',') if n.strip()))[:30]:
                        conn.execute(f'INSERT OR IGNORE INTO {table}(name) VALUES(?)',(name,))
                        id=conn.execute(f'SELECT id FROM {table} WHERE name=?',(name,)).fetchone()[0]
                        conn.execute(f'INSERT INTO {link}(book_id,{fk}) VALUES(?,?)',(book_id,id))
                elif action=='enrich':
                    if not book['isbn']: raise ValueError('Bitte zuerst eine ISBN hinterlegen')
                    updates={}
                    for field in ('title','author','description','cover_url','publisher','published','pages'):
                        if not book[field] and meta.get(field):
                            updates[field]=meta[field]
                    if updates:
                        conn.execute('UPDATE books SET '+','.join(f'{key}=?' for key in updates)+' WHERE id=?',
                                     list(updates.values())+[book_id])
                    else:
                        flash('Keine fehlenden Informationen gefunden.','info')
                elif action=='metadata':
                    conn.execute('UPDATE books SET title=?,author=?,isbn=?,category=?,description=?,cover_url=? WHERE id=?',tuple(request.form.get(x,'') for x in ('title','author','isbn','category','description','cover_url'))+(book_id,))
                else: abort(400)
            flash('Gespeichert.','success')
        except (ValueError,sqlite3.IntegrityError) as e: flash(str(e),'danger')
        return redirect(url_for('.detail',book_id=book_id))
    copies=conn.execute('SELECT * FROM book_copies WHERE book_id=? ORDER BY copy_number',(book_id,)).fetchall()
    terms={}
    for table,link,fk in [('tags','book_tags','tag_id'),('genres','book_genres','genre_id')]:
        terms[table]=', '.join(r[0] for r in conn.execute(f'SELECT t.name FROM {table} t JOIN {link} l ON t.id=l.{fk} WHERE l.book_id=?',(book_id,)))
    return view('''<div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
    <div><h1 class="h3 mb-1">{{book.title}}</h1><p class="text-muted mb-0">{{book.author}}</p></div>
    <a class="btn btn-outline-secondary" href="{{url_for('book_detail',book_id=book.id)}}">Buchansicht öffnen</a></div>
    <div class="row g-4"><div class="col-lg-7">
    <h2 class="h5">Buchinformationen</h2><p class="text-muted small">Ergänze eine ISBN und speichere sie. Danach kannst du die fehlenden Angaben automatisch laden.</p>
    <form method="post" class="mb-3"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><input type="hidden" name="action" value="metadata">
    {% for f,label in [("title","Titel"),("author","Autor"),("isbn","ISBN"),("category","Kategorie"),("description","Beschreibung"),("cover_url","Cover-URL")] %}
    <div class="mb-3"><label class="form-label" for="field-{{f}}">{{label}}</label>
    {% if f=="description" %}<textarea class="form-control" id="field-{{f}}" name="{{f}}" rows="4">{{book[f] or ''}}</textarea>
    {% else %}<input class="form-control" id="field-{{f}}" name="{{f}}" value="{{book[f] or ''}}">{% endif %}</div>{% endfor %}
    <button class="btn btn-primary">Änderungen speichern</button></form>
    <form method="post" class="mb-4"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><input type="hidden" name="action" value="enrich"><button class="btn btn-outline-primary" {% if not book.isbn %}disabled{% endif %}>Fehlende Angaben und Cover per ISBN laden</button></form>
    {% for t,label in [("genres","Genres"),("tags","Schlagwörter")] %}
    <form method="post" class="mb-3"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><input type="hidden" name="action" value="{{t}}"><label class="form-label">{{label}} (durch Komma getrennt)</label><div class="input-group"><input class="form-control" name="names" value="{{terms[t]}}"><button class="btn btn-outline-secondary">Speichern</button></div></form>{% endfor %}
    </div><div class="col-lg-5"><h2 class="h5">Cover und Exemplare</h2>
    {% if book.cover_url %}<img src="{{book.cover_url}}" alt="Cover" class="img-thumbnail mb-3" style="max-height:190px">{% endif %}
    <p class="small text-muted">Jedes physische Exemplar kann eine eigene Inventarnummer und einen Standort haben.</p>
    {% for c in copies %}<details class="border rounded p-3 mb-2"><summary class="fw-semibold">Exemplar {{c.copy_number}} · {{c.inventory_code}}</summary>
    <form method="post" class="mt-3"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><input type="hidden" name="action" value="copy"><input type="hidden" name="copy_id" value="{{c.id}}">
    {% for f,label in [("inventory_code","Inventarnummer"),("barcode","Barcode"),("location","Standort"),("shelf","Regal"),("notes","Notizen")] %}<label class="form-label small">{{label}}<input class="form-control" name="{{f}}" value="{{c[f] or ''}}"></label>{% endfor %}
    <label class="form-label">Zustand<select class="form-select" name="condition">{% for v,label in [("new","Neu"),("good","Gut"),("worn","Gebraucht"),("damaged","Beschädigt")] %}<option value="{{v}}" {% if c.condition==v %}selected{% endif %}>{{label}}</option>{% endfor %}</select></label>
    <button class="btn btn-primary btn-sm d-block mt-2">Exemplar speichern</button></form></details>{% endfor %}
    <details class="border rounded p-3 mt-3"><summary class="fw-semibold">Weiteres Exemplar hinzufügen</summary>
    <form method="post" class="mt-3"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><input type="hidden" name="action" value="add_copy">
    {% for f,label in [("inventory_code","Inventarnummer (optional)"),("barcode","Barcode"),("location","Standort"),("shelf","Regal")] %}<label class="form-label d-block">{{label}}<input class="form-control" name="{{f}}"></label>{% endfor %}
    <label class="form-label">Zustand<select class="form-select" name="condition"><option value="good">Gut</option><option value="new">Neu</option><option value="worn">Gebraucht</option><option value="damaged">Beschädigt</option></select></label>
    <button class="btn btn-primary d-block">Exemplar hinzufügen</button></form></details></div></div>''',book=book,copies=copies,terms=terms)
