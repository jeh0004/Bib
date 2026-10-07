
import json, secrets, sqlite3, re, unicodedata, os, html
from urllib.request import Request, urlopen
from urllib.parse import urlencode, urljoin, urlparse
from flask import Blueprint, abort, flash, redirect, render_template, render_template_string, request, session, url_for
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
    """Find edition metadata by ISBN. Preserve source accuracy and missing values."""
    from urllib.parse import urlencode
    from urllib.error import HTTPError, URLError
    digits=''.join(ch for ch in isbn.upper() if ch.isdigit() or ch=='X')
    if len(digits) not in (10,13):
        raise ValueError('ISBN ungültig')
    def fetch(url):
        with urlopen(Request(url, headers={'User-Agent':'LeihGut/1.0 (library catalog)'}),
                     timeout=6) as response:
            return json.load(response)
    data={}
    try:
        result=fetch('https://openlibrary.org/api/books?'+urlencode(
            {'bibkeys':'ISBN:'+digits,'jscmd':'data','format':'json'}))
        entry=result.get('ISBN:'+digits) or {}
        if entry:
            cover=entry.get('cover') or {}
            excerpts=entry.get('excerpts') or []
            desc=excerpts[0].get('text','') if excerpts else ''
            if isinstance(desc,dict): desc=desc.get('value','')
            data=dict(title=entry.get('title',''),
                      author=', '.join(a.get('name','') for a in entry.get('authors',[])[:5]),
                      isbn=digits,description=desc,
                      cover_url=cover.get('large') or cover.get('medium') or '',
                      publisher=', '.join(x.get('name','') for x in entry.get('publishers',[])[:3]),
                      published=entry.get('publish_date',''),
                      pages=entry.get('number_of_pages'))
    except (HTTPError,URLError,TimeoutError,ValueError, OSError):
        pass
    # Google Books can fill gaps for specialized titles not held by Open Library.
    if not all(data.get(k) for k in ('title','author','cover_url','publisher','published','description')):
        try:
            result=fetch('https://www.googleapis.com/books/v1/volumes?'+urlencode(
                {'q':'isbn:'+digits,'maxResults':5}))
            for volume in result.get('items',[]):
                info=volume.get('volumeInfo') or {}
                identifiers=info.get('industryIdentifiers') or []
                normalized={''.join(c for c in item.get('identifier','').upper()
                                    if c.isdigit() or c=='X') for item in identifiers}
                if digits not in normalized:
                    continue
                covers=info.get('imageLinks') or {}
                cover=covers.get('thumbnail') or covers.get('smallThumbnail') or ''
                if cover.startswith('http://'):
                    cover='https://'+cover[7:]
                supplement=dict(title=info.get('title',''),
                    author=', '.join(info.get('authors') or []),
                    description=info.get('description',''),
                    cover_url=cover,
                    publisher=info.get('publisher',''),
                    published=info.get('publishedDate',''),
                    pages=info.get('pageCount'))
                for key,value in supplement.items():
                    if not data.get(key) and value: data[key]=value
                break
        except (HTTPError,URLError,TimeoutError,ValueError,OSError):
            pass
    if not data.get('title'):
        raise ValueError('Keine Informationen zu dieser ISBN gefunden')
    data['isbn']=digits
    return data


def matching_title(local, remote):
    """Reject covers for editions whose titles refer to different works."""
    def tokens(value):
        value=unicodedata.normalize('NFKD',value.casefold())
        value=''.join(c for c in value if not unicodedata.combining(c))
        return set(re.findall(r'[a-z0-9]{3,}',value))-{'und','der','die','das','ein','eine','von','mit','fur','fuer','auflage','band','alpin'}
    a,b=tokens(local or ''),tokens(remote or '')
    if not a or not b:
        return False
    return len(a & b)/min(len(a),len(b)) >= 0.65

def safe_enrichment(book, meta):
    """Never copy metadata or covers from a clearly different work."""
    if not matching_title(book['title'],meta.get('title','')):
        return {}
    return {field:meta[field] for field in ('description','cover_url','publisher','published','pages')
            if not book[field] and meta.get(field)}



def _normal_isbn(value):
    return ''.join(c for c in (value or '').upper() if c.isdigit() or c == 'X')

def _author_matches(local, remote):
    local=(local or '').strip()
    remote=(remote or '').strip()
    if not local or local.casefold()=='unbekannt' or not remote:
        return False
    local_parts=[x.strip() for x in re.split(r'[,;/]| und ',local) if x.strip()]
    return any(matching_title(part,remote) for part in local_parts)

def _publisher_matches(local, remote):
    def words(value):
        value=unicodedata.normalize('NFKD',(value or '').casefold())
        return {x for x in re.findall(r'[a-z0-9]{3,}',value)
                if x not in {'verlag','bergverlag','gmbh','edition'}}
    a,b=words(local),words(remote)
    return bool(a and b and a & b)


def _html_get(url, timeout=7):
    with urlopen(Request(url,headers={
        'User-Agent':'Mozilla/5.0 (compatible; LeihGut/2.0; library cover review)'
    }),timeout=timeout) as response:
        raw=response.read(900000)
        charset=response.headers.get_content_charset() or 'utf-8'
        return raw.decode(charset,'replace')

def _extract_links(page, base_url, allowed_host):
    links=[]
    for href in re.findall(r'''(?is)<a\b[^>]*\bhref=["']([^"'#]+)["']''',page):
        href=html.unescape(href).strip()
        full=urljoin(base_url,href)
        p=urlparse(full)
        if p.scheme!='https' or p.netloc.lower()!=allowed_host.lower():
            continue
        if full not in links:
            links.append(full)
    return links

def _meta_value(page, key):
    patterns=[
        rf'''(?is)<meta\b[^>]*(?:property|name|itemprop)=["']{re.escape(key)}["'][^>]*content=["']([^"']+)["']''',
        rf'''(?is)<meta\b[^>]*content=["']([^"']+)["'][^>]*(?:property|name|itemprop)=["']{re.escape(key)}["']'''
    ]
    for pattern in patterns:
        m=re.search(pattern,page)
        if m:
            return html.unescape(m.group(1)).strip()
    return ''

def _specialist_product(url, source, local):
    try:
        page=_html_get(url)
    except Exception:
        return None
    title=_meta_value(page,'og:title') or _meta_value(page,'twitter:title')
    image=_meta_value(page,'og:image') or _meta_value(page,'twitter:image') or _meta_value(page,'image')
    if not image:
        m=re.search(r'''(?is)"image"\s*:\s*(?:\[\s*)?["']([^"']+)["']''',page)
        image=html.unescape(m.group(1)).strip() if m else ''
    if image.startswith('//'): image='https:'+image
    elif image: image=urljoin(url,image)
    if not image.startswith('https://'):
        return None
    text=re.sub(r'<[^>]+>',' ',page)
    text=html.unescape(re.sub(r'\s+',' ',text))
    local_isbn=_normal_isbn(local['isbn'] if 'isbn' in local.keys() else '')
    exact=bool(local_isbn and local_isbn in _normal_isbn(text))
    title_ok=matching_title(local['title'],title or text[:500])
    if not (exact or title_ok):
        return None
    score=(72 if exact else 0)+(18 if title_ok else 0)
    if _author_matches(local['author'],text): score+=6
    if _publisher_matches(local['publisher'] if 'publisher' in local.keys() else '',text): score+=4
    return dict(source=source,title=title or local['title'],authors='',
                cover_url=image,isbn=local_isbn if exact else '',publisher='',
                confidence=min(score,94),exact_isbn=exact,query='Spezialquelle',
                auto_eligible=False,product_url=url)

def specialist_cover_candidates(book):
    """Publisher/specialist-shop search. Candidates are always manual-review only."""
    from urllib.error import HTTPError, URLError
    local_isbn=_normal_isbn(book['isbn'] if 'isbn' in book.keys() else '')
    title=(book['title'] or '').strip()
    search_term=local_isbn or title
    if not search_term:
        return []
    configs=[
        ('Bergverlag Rother','https://www.rother.de',
         'https://www.rother.de/de/catalogsearch/result/?q={q}',
         lambda u:'/de/' in urlparse(u).path and u.endswith('.html')),
        ('Panico Alpinverlag','https://www.panico.de',
         'https://www.panico.de/catalogsearch/result/?q={q}',
         lambda u:u.endswith('.html') and '/media/' not in u),
        ('Das Landkartenhaus','https://www.das-landkartenhaus.de',
         'https://www.das-landkartenhaus.de/search?search={q}',
         lambda u:'/search' not in urlparse(u).path and len(urlparse(u).path)>2),
        ('ZVAB','https://www.zvab.com',
         'https://www.zvab.com/servlet/SearchResults?isbn={q}',
         lambda u:'/servlet/' not in urlparse(u).path and ('/plp' in u or '978' in u)),
        ('Freytag & Berndt','https://www.freytagberndt.com',
         'https://www.freytagberndt.com/en/catalogsearch/result/?q={q}',
         lambda u:u.endswith('.html') and '/catalogsearch/' not in u),
        ('Preigu','https://preigu.de',
         'https://preigu.de/search?sSearch={q}',
         lambda u:'/search' not in urlparse(u).path and '/buecher/' in u),
    ]
    found=[]
    curated={
        '9783859022904': [
            ('Die Buchsuche','https://diebuchsuche.de/buch-9783859022904.html'),
        ],
        '9783859022522': [
            ('ZVAB','https://www.zvab.com/9783859022522/Alpinf%C3%BChrer-B%C3%BCndner-Alpen-S%C3%BCdliches-Bergell-3859022520/plp'),
            ('Preigu','https://preigu.de/buecher/clubfuehrer-buendner-alpen-4/101448054'),
        ],
        '9783859022126': [
            ('ZVAB','https://www.zvab.com/9783859022126/BUENDNER-ALPEN-5-BERNINA-GRUPPE-ING-3859022121/plp'),
            ('Freytag & Berndt','https://www.freytagberndt.com/en/clubfuhrer-bundner-alpen-5.html'),
        ],
        '9783763361052': [
            ('Bergverlag Rother','https://www.rother.de/de/thema/lehrbucher/alpin-lehrplan-7.html'),
        ],
    }
    # Known, externally verified edition pages are tried first. They are still
    # review-only and never eligible for automatic acceptance.
    for source,url in curated.get(local_isbn,[]):
        item=_specialist_product(url,source,book)
        if item and not any(x['cover_url']==item['cover_url'] for x in found):
            found.append(item)
    for source,base,pattern,is_product in configs:
        try:
            search_url=pattern.format(q=urlencode({'x':search_term})[2:])
            page=_html_get(search_url)
            host=urlparse(base).netloc
            links=[u for u in _extract_links(page,base,host) if is_product(u)][:8]
            for link in links:
                item=_specialist_product(link,source,book)
                if item and not any(x['cover_url']==item['cover_url'] for x in found):
                    found.append(item)
                    if item['exact_isbn']:
                        break
        except (HTTPError,URLError,TimeoutError,ValueError,OSError,TypeError):
            continue
    return sorted(found,key=lambda x:(x['exact_isbn'],x['confidence']),reverse=True)[:8]

def expanded_cover_candidates(book):
    """Search multiple public book indexes and score candidates conservatively."""
    from urllib.parse import urlencode
    from urllib.error import HTTPError, URLError
    title=(book['title'] or '').strip()
    author=(book['author'] or '').strip()
    publisher=(book['publisher'] or '').strip() if 'publisher' in book.keys() else ''
    local_isbn=_normal_isbn(book['isbn'] if 'isbn' in book.keys() else '')
    results=[]

    def add(source,c_title,c_author,cover,isbn='',c_publisher='',query=''):
        if not cover or not str(cover).startswith('https://'):
            return
        if any(x['cover_url']==cover for x in results):
            return
        cand_isbns=[_normal_isbn(x) for x in re.split(r'[,; ]+',isbn or '') if _normal_isbn(x)]
        exact_isbn=bool(local_isbn and local_isbn in cand_isbns)
        title_ok=matching_title(title,c_title)
        author_ok=_author_matches(author,c_author)
        publisher_ok=_publisher_matches(publisher,c_publisher)
        if not title_ok and not exact_isbn:
            return
        score=(62 if exact_isbn else 0)+(25 if title_ok else 0)+(9 if author_ok else 0)+(4 if publisher_ok else 0)
        # Unknown authors are common in the legacy catalog; exact ISBN + title is enough for auto use.
        if author.casefold()=='unbekannt' and exact_isbn and title_ok:
            score=max(score,95)
        results.append(dict(source=source,title=c_title,authors=c_author,cover_url=cover,
                            isbn=isbn,publisher=c_publisher,confidence=min(score,100),
                            exact_isbn=exact_isbn,query=query,auto_eligible=True,product_url=''))

    def fetch(url):
        with urlopen(Request(url,headers={'User-Agent':'LeihGut/2.0 (DAV library cover matching)'}),timeout=7) as response:
            return json.load(response)

    queries=[]
    if local_isbn:
        queries.append(('isbn',local_isbn))
    if title and author and author.casefold()!='unbekannt':
        queries.append(('title_author',(title,author)))
    if title and publisher:
        queries.append(('title_publisher',(title,publisher)))
    if title:
        queries.append(('title',title))

    # Open Library: exact ISBN plus progressively broader title searches.
    try:
        if local_isbn:
            data=fetch('https://openlibrary.org/api/books?'+urlencode(
                {'bibkeys':'ISBN:'+local_isbn,'jscmd':'data','format':'json'}))
            entry=data.get('ISBN:'+local_isbn) or {}
            cover=entry.get('cover') or {}
            if entry and cover:
                add('Open Library',entry.get('title',''),
                    ', '.join(a.get('name','') for a in entry.get('authors',[])),
                    cover.get('large') or cover.get('medium') or '',
                    local_isbn,', '.join(p.get('name','') for p in entry.get('publishers',[])),'ISBN')
    except (HTTPError,URLError,TimeoutError,ValueError,OSError,TypeError):
        pass
    for mode,value in queries[1:] if local_isbn else queries:
        try:
            params={'fields':'title,author_name,cover_i,isbn,publisher','limit':10}
            if mode=='title_author':
                params.update(title=value[0],author=value[1])
            elif mode=='title_publisher':
                params.update(title=value[0],publisher=value[1])
            else:
                params.update(title=value)
            data=fetch('https://openlibrary.org/search.json?'+urlencode(params))
            for doc in data.get('docs',[]):
                cid=doc.get('cover_i')
                if not cid: continue
                add('Open Library',doc.get('title',''),', '.join(doc.get('author_name') or []),
                    f'https://covers.openlibrary.org/b/id/{int(cid)}-L.jpg',
                    ', '.join((doc.get('isbn') or [])[:5]),
                    ', '.join((doc.get('publisher') or [])[:3]),mode)
        except (HTTPError,URLError,TimeoutError,ValueError,OSError,TypeError):
            pass

    # Google Books: exact ISBN, then title/author, title/publisher and title-only.
    google_queries=[]
    if local_isbn: google_queries.append(('ISBN',f'isbn:{local_isbn}'))
    if title and author and author.casefold()!='unbekannt':
        google_queries.append(('Titel+Autor',f'intitle:{title} inauthor:{author}'))
    if title and publisher:
        google_queries.append(('Titel+Verlag',f'intitle:{title} inpublisher:{publisher}'))
    if title: google_queries.append(('Titel',f'intitle:{title}'))
    for label,q in google_queries:
        try:
            data=fetch('https://www.googleapis.com/books/v1/volumes?'+urlencode(
                {'q':q,'maxResults':10,'printType':'books'}))
            for volume in data.get('items',[]):
                info=volume.get('volumeInfo') or {}
                covers=info.get('imageLinks') or {}
                cover=covers.get('extraLarge') or covers.get('large') or covers.get('medium') or covers.get('thumbnail') or ''
                if cover.startswith('http://'): cover='https://'+cover[7:]
                add('Google Books',info.get('title',''),', '.join(info.get('authors') or []),cover,
                    ', '.join(x.get('identifier','') for x in info.get('industryIdentifiers') or []),
                    info.get('publisher',''),label)
        except (HTTPError,URLError,TimeoutError,ValueError,OSError,TypeError):
            pass

    for item in specialist_cover_candidates(book):
        if not any(x['cover_url']==item['cover_url'] for x in results):
            results.append(item)
    return sorted(results,key=lambda x:(x['confidence'],x['exact_isbn']),reverse=True)[:12]

def _save_candidates(conn,book,candidates):
    conn.execute("UPDATE cover_candidates SET status='stale' WHERE book_id=? AND status='pending'",(book['id'],))
    for item in candidates:
        conn.execute("""INSERT INTO cover_candidates
            (book_id,cover_url,source,candidate_title,candidate_author,candidate_isbn,source_url,confidence,status)
            VALUES(?,?,?,?,?,?,?,?,'pending')
            ON CONFLICT(book_id,cover_url) DO UPDATE SET
              source=excluded.source,candidate_title=excluded.candidate_title,
              candidate_author=excluded.candidate_author,candidate_isbn=excluded.candidate_isbn,
              source_url=excluded.source_url,confidence=excluded.confidence,
              status='pending',created_at=datetime('now')""",
            (book['id'],item['cover_url'],item['source'],item['title'],item['authors'],item['isbn'],
             item.get('product_url',''),item['confidence']))

def _apply_cover(conn,book_id,url,source,status,confidence):
    conn.execute("""UPDATE books SET cover_url=?,cover_source=?,cover_status=?,
                    cover_confidence=?,cover_checked_at=datetime('now') WHERE id=?""",
                 (url,source,status,confidence,book_id))
    conn.execute("UPDATE cover_candidates SET status='rejected' WHERE book_id=? AND status='pending'",(book_id,))

def _valid_cover_upload(storage):
    filename=(storage.filename or '').lower()
    ext=filename.rsplit('.',1)[-1] if '.' in filename else ''
    if ext not in {'jpg','jpeg','png','webp'}:
        raise ValueError('Bitte ein JPG-, PNG- oder WebP-Bild hochladen.')
    head=storage.stream.read(16)
    storage.stream.seek(0)
    ok=(head.startswith(b'\xff\xd8\xff') or head.startswith(b'\x89PNG\r\n\x1a\n')
        or (len(head)>=12 and head[:4]==b'RIFF' and head[8:12]==b'WEBP'))
    if not ok:
        raise ValueError('Die hochgeladene Datei ist kein gültiges Coverbild.')
    return 'jpg' if ext=='jpeg' else ext

def cover_suggestions(book):
    """Suggestions only: title/author search is not proof of the same edition."""
    from urllib.parse import urlencode
    from urllib.error import HTTPError, URLError
    title, author=book['title'] or '',book['author'] or ''
    if not title.strip() or not author.strip():
        return []
    suggestions=[]
    def accept(source, candidate_title, candidate_authors, cover, isbn=''):
        if not cover or not matching_title(title,candidate_title):
            return
        if not any(matching_title(a, candidate_authors) for a in author.split(',') if a.strip()):
            return
        if not cover.startswith('https://'):
            return
        if any(x['cover_url']==cover for x in suggestions):
            return
        suggestions.append(dict(source=source,title=candidate_title,authors=candidate_authors,
                                cover_url=cover,isbn=isbn))
    def fetch(url):
        with urlopen(Request(url,headers={'User-Agent':'LeihGut/1.0 (library catalog)'}),timeout=6) as response:
            return json.load(response)
    try:
        result=fetch('https://openlibrary.org/search.json?'+urlencode({
            'title':title,'author':author,'fields':'title,author_name,cover_i,isbn',
            'limit':8}))
        for doc in result.get('docs',[]):
            cover=doc.get('cover_i')
            if cover:
                accept('Open Library',doc.get('title',''),
                       ', '.join(doc.get('author_name') or []),
                       f'https://covers.openlibrary.org/b/id/{int(cover)}-L.jpg',
                       ', '.join((doc.get('isbn') or [])[:2]))
    except (HTTPError,URLError,TimeoutError,ValueError,OSError,TypeError):
        pass
    try:
        result=fetch('https://www.googleapis.com/books/v1/volumes?'+urlencode({
            'q':f'intitle:{title} inauthor:{author}','maxResults':8}))
        for volume in result.get('items',[]):
            info=volume.get('volumeInfo') or {}
            cover=(info.get('imageLinks') or {}).get('thumbnail','')
            if cover.startswith('http://'): cover='https://'+cover[7:]
            accept('Google Books',info.get('title',''),
                   ', '.join(info.get('authors') or []),cover,
                   ', '.join(i.get('identifier','') for i in info.get('industryIdentifiers') or []))
    except (HTTPError,URLError,TimeoutError,ValueError,OSError,TypeError):
        pass
    return suggestions[:8]


@bp.get('/')
def index():
    books=db().execute('SELECT b.*,COUNT(c.id) copies FROM books b LEFT JOIN book_copies c ON b.id=c.book_id GROUP BY b.id ORDER BY b.title').fetchall()
    stats=db().execute("""SELECT COUNT(*) total,
        SUM(CASE WHEN cover_url IS NOT NULL AND TRIM(cover_url)!='' THEN 1 ELSE 0 END) covered,
        SUM(CASE WHEN cover_status='review' THEN 1 ELSE 0 END) review
        FROM books""").fetchone()
    return view('''<h1 class="h3">Bücher verwalten</h1><p class="text-muted">ISBN-Daten, Cover und Exemplare verwalten.</p>
    <div class="row g-2 mb-3">
      <div class="col-auto"><span class="badge text-bg-light border fs-6">{{stats.covered or 0}} / {{stats.total}} mit Cover</span></div>
      <div class="col-auto"><span class="badge text-bg-warning fs-6">{{stats.review or 0}} zur Prüfung</span></div>
    </div>
    <div class="d-flex flex-wrap gap-2 mb-4"><a class="btn btn-primary" href="{{url_for('catalog_extra.add')}}">Buch hinzufügen</a>
    <a class="btn btn-outline-secondary" href="{{url_for('catalog_extra.inventory')}}">Inventarliste</a>
    <a class="btn btn-outline-warning" href="{{url_for('catalog_extra.cover_review')}}">Cover prüfen</a>
    <form method="post" action="{{url_for('catalog_extra.scan_covers')}}"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><button class="btn btn-outline-success">Fehlende Cover suchen (bis zu 10)</button></form>
    <form method="post" action="{{url_for('catalog_extra.enrich_missing')}}"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><button class="btn btn-outline-primary">Fehlende ISBN-Daten ergänzen (bis zu 10)</button></form></div>
    <div class="list-group">{% for b in books %}<a class="list-group-item list-group-item-action d-flex align-items-center gap-3" href="{{url_for('catalog_extra.detail',book_id=b.id)}}">{% if b.cover_url %}<img src="{{b.cover_url}}" width="42" height="62" style="object-fit:contain" alt="">{% else %}<span class="fs-2">📖</span>{% endif %}<span class="flex-grow-1"><strong>{{b.title}}</strong><br><span class="text-muted small">{{b.author}} · {{b.copies}} Exemplare{% if b.cover_status=='review' %} · Cover prüfen{% endif %}</span></span><span aria-hidden="true">›</span></a>{% endfor %}</div>''',books=books,stats=stats)
@bp.post('/enrich-missing')
def enrich_missing():
    conn=db()
    after_id=session.get('enrich_after_id',0)
    rows=conn.execute("""SELECT id,isbn,title,author,description,cover_url,publisher,published,pages
        FROM books WHERE isbn IS NOT NULL AND TRIM(isbn)!=''
        AND (cover_url IS NULL OR TRIM(cover_url)='' OR publisher IS NULL OR TRIM(publisher)=''
             OR published IS NULL OR TRIM(published)='' OR description IS NULL OR TRIM(description)='')
        AND id > ? ORDER BY id LIMIT 10""",(after_id,)).fetchall()
    if not rows:
        session['enrich_after_id']=0
        flash('Alle Bücher wurden einmal geprüft. Mit erneutem Klick beginnt ein neuer Durchlauf.','info')
        return redirect(url_for('.index'))
    changed=0; failed=0
    for book in rows:
        try:
            meta=isbn_lookup(book['isbn'])
            updates=safe_enrichment(book,meta)
            if updates:
                with conn:
                    conn.execute('UPDATE books SET '+','.join(f'{field}=?' for field in updates)+' WHERE id=?',
                                 list(updates.values())+[book['id']])
                changed+=1
        except Exception:
            failed+=1
    if rows: session['enrich_after_id']=rows[-1]['id']
    flash(f'{changed} Bücher ergänzt, {failed} ISBN-Abfragen ohne Treffer oder mit Fehler. Nächste Gruppe mit erneutem Klick.','info')
    return redirect(url_for('.index'))

@bp.post('/covers/scan')
def scan_covers():
    conn=db()
    after_id=session.get('cover_scan_after_id',0)
    rows=conn.execute("""SELECT * FROM books
        WHERE (cover_url IS NULL OR TRIM(cover_url)='')
          AND id>? ORDER BY id LIMIT 10""",(after_id,)).fetchall()
    if not rows:
        session['cover_scan_after_id']=0
        flash('Alle Bücher ohne Cover wurden einmal durchsucht. Mit erneutem Klick beginnt ein neuer Durchlauf.','info')
        return redirect(url_for('.cover_review'))
    auto=0; review=0; none=0
    for book in rows:
        candidates=expanded_cover_candidates(book)
        with conn:
            _save_candidates(conn,book,candidates)
            if candidates:
                best=candidates[0]
                second=candidates[1]['confidence'] if len(candidates)>1 else -1
                if (best['confidence']>=95 and best['exact_isbn']
                    and best.get('auto_eligible',True)
                    and best['confidence']-second>=5):
                    _apply_cover(conn,book['id'],best['cover_url'],best['source'],'auto',best['confidence'])
                    auto+=1
                else:
                    conn.execute("""UPDATE books SET cover_status='review',
                                  cover_checked_at=datetime('now') WHERE id=?""",(book['id'],))
                    review+=1
            else:
                conn.execute("""UPDATE books SET cover_status='missing',
                              cover_checked_at=datetime('now') WHERE id=?""",(book['id'],))
                none+=1
    session['cover_scan_after_id']=rows[-1]['id']
    flash(f'Cover-Suche: {auto} sicher übernommen, {review} zur Prüfung, {none} ohne Treffer.','info')
    return redirect(url_for('.cover_review'))

@bp.get('/covers/review')
def cover_review():
    conn=db()
    rows=conn.execute("""SELECT b.*,
        (SELECT COUNT(*) FROM cover_candidates c WHERE c.book_id=b.id AND c.status='pending') AS candidate_count
        FROM books b
        WHERE b.cover_status='review'
           OR ((b.cover_url IS NULL OR TRIM(b.cover_url)='') AND EXISTS(
               SELECT 1 FROM cover_candidates c WHERE c.book_id=b.id AND c.status='pending'))
        ORDER BY COALESCE(b.cover_checked_at,''),b.title LIMIT 40""").fetchall()
    pending=[]
    for book in rows:
        candidates=conn.execute("""SELECT * FROM cover_candidates
            WHERE book_id=? AND status='pending'
            ORDER BY confidence DESC,id DESC LIMIT 6""",(book['id'],)).fetchall()
        pending.append((book,candidates))
    counts=conn.execute("""SELECT
        COUNT(*) total,
        SUM(CASE WHEN cover_url IS NOT NULL AND TRIM(cover_url)!='' THEN 1 ELSE 0 END) covered,
        SUM(CASE WHEN cover_status='review' THEN 1 ELSE 0 END) review,
        SUM(CASE WHEN cover_url IS NULL OR TRIM(cover_url)='' THEN 1 ELSE 0 END) missing
        FROM books""").fetchone()
    return render_template('admin_cover_review.html',pending=pending,counts=counts)

@bp.post('/covers/<int:book_id>/choose/<int:candidate_id>')
def choose_candidate(book_id,candidate_id):
    conn=db()
    candidate=conn.execute("""SELECT * FROM cover_candidates
                              WHERE id=? AND book_id=? AND status='pending'""",
                           (candidate_id,book_id)).fetchone()
    if not candidate: abort(404)
    with conn:
        _apply_cover(conn,book_id,candidate['cover_url'],candidate['source'],'reviewed',candidate['confidence'])
        conn.execute("UPDATE cover_candidates SET status='chosen' WHERE id=?",(candidate_id,))
    flash('Cover übernommen.','success')
    return redirect(request.form.get('next') or url_for('.cover_review'))

@bp.post('/covers/<int:book_id>/reject')
def reject_candidates(book_id):
    conn=db()
    with conn:
        conn.execute("UPDATE cover_candidates SET status='rejected' WHERE book_id=? AND status='pending'",(book_id,))
        conn.execute("""UPDATE books SET cover_status='missing',cover_checked_at=datetime('now')
                        WHERE id=? AND (cover_url IS NULL OR TRIM(cover_url)='')""",(book_id,))
    flash('Vorschläge verworfen. Das Buch bleibt für eine spätere Suche oder einen Upload offen.','info')
    return redirect(request.form.get('next') or url_for('.cover_review'))

@bp.post('/covers/<int:book_id>/upload')
def upload_cover(book_id):
    conn=db()
    book=conn.execute('SELECT id,title,cover_url FROM books WHERE id=?',(book_id,)).fetchone()
    if not book: abort(404)
    storage=request.files.get('cover_file')
    if not storage or not storage.filename:
        flash('Bitte eine Coverdatei auswählen.','danger')
        return redirect(request.form.get('next') or url_for('.detail',book_id=book_id))
    try:
        ext=_valid_cover_upload(storage)
        from app import UPLOAD_DIR
        os.makedirs(UPLOAD_DIR,exist_ok=True)
        filename=f'book-cover-{book_id}-{secrets.token_hex(8)}.{ext}'
        storage.save(os.path.join(UPLOAD_DIR,filename))
        with conn:
            _apply_cover(conn,book_id,url_for('serve_upload',filename=filename),'Manueller Upload','manual',100)
        flash('Eigenes Cover gespeichert.','success')
    except (ValueError,OSError) as exc:
        flash(str(exc),'danger')
    return redirect(request.form.get('next') or url_for('.detail',book_id=book_id))

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
                cur=conn.execute('INSERT INTO books(title,author,isbn,category,description,total_copies,cover_url,publisher,published,pages,area,topic,book_index) VALUES(?,?,?,?,?,0,?,?,?,?,?,?,?)',(title,author,request.form.get('isbn',''),request.form.get('category',''),request.form.get('description',''),request.form.get('cover_url',''),request.form.get('publisher',''),request.form.get('published',''),request.form.get('pages') or None,request.form.get('area',''),request.form.get('topic',''),request.form.get('book_index','')))
            return redirect(url_for('.detail',book_id=cur.lastrowid))
        flash('Titel und Autor sind erforderlich.','danger')
    return view('<h1>Buch anlegen</h1><form method="get"><input name="isbn" placeholder="ISBN"><button>ISBN suchen</button></form><form method="post"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}">{% for field in ["title","author","isbn","category","cover_url","description","publisher","published","pages","area","topic","book_index"] %}<p><label>{{field}} <input name="{{field}}" value="{{meta.get(field,"")}}"></label></p>{% endfor %}<button>Speichern</button></form>',meta=meta)
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
                    updates=safe_enrichment(book,meta)
                    if updates:
                        conn.execute('UPDATE books SET '+','.join(f'{key}=?' for key in updates)+' WHERE id=?',
                                     list(updates.values())+[book_id])
                    else:
                        flash('Keine fehlenden Informationen gefunden.','info')
                elif action=='choose_cover':
                    selected=request.form.get('cover_url','')
                    matches=expanded_cover_candidates(book)
                    match=next((x for x in matches if x['cover_url']==selected),None)
                    if not match:
                        raise ValueError('Cover-Vorschlag nicht mehr verfügbar')
                    _apply_cover(conn,book_id,selected,match['source'],'reviewed',match['confidence'])
                elif action=='remove_cover':
                    conn.execute("""UPDATE books SET cover_url=NULL,cover_source=NULL,
                                   cover_status='missing',cover_confidence=NULL,
                                   cover_checked_at=datetime('now') WHERE id=?""",(book_id,))
                elif action=='metadata':
                    conn.execute('UPDATE books SET title=?,author=?,isbn=?,category=?,description=?,cover_url=?,publisher=?,published=?,pages=?,area=?,topic=?,book_index=? WHERE id=?',
                                 tuple(request.form.get(x,'') for x in ('title','author','isbn','category','description','cover_url','publisher','published'))+
                                 ((int(request.form.get('pages')) if request.form.get('pages','').isdigit() else None),
                                  request.form.get('area',''),request.form.get('topic',''),request.form.get('book_index',''),book_id))
                else: abort(400)
            flash('Gespeichert.','success')
        except (ValueError,sqlite3.IntegrityError) as e: flash(str(e),'danger')
        return redirect(url_for('.detail',book_id=book_id))
    suggestions=expanded_cover_candidates(book) if request.args.get('cover_search')=='1' and not book['cover_url'] else []
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
    {% for f,label in [("title","Titel"),("author","Autor"),("isbn","ISBN"),("category","Kategorie"),("publisher","Verlag"),("published","Erscheinungsjahr / Auflage"),("pages","Seiten"),("area","Gebiet"),("topic","Thema"),("book_index","Buchindex"),("description","Beschreibung"),("cover_url","Cover-URL")] %}
    <div class="mb-3"><label class="form-label" for="field-{{f}}">{{label}}</label>
    {% if f=="description" %}<textarea class="form-control" id="field-{{f}}" name="{{f}}" rows="4">{{book[f] or ''}}</textarea>
    {% else %}<input class="form-control" id="field-{{f}}" name="{{f}}" value="{{book[f] or ''}}">{% endif %}</div>{% endfor %}
    <button class="btn btn-primary">Änderungen speichern</button></form>
    <form method="post" class="mb-4"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><input type="hidden" name="action" value="enrich"><button class="btn btn-outline-primary" {% if not book.isbn %}disabled{% endif %}>Fehlende Angaben und Cover per ISBN laden</button></form>
    {% for t,label in [("genres","Genres"),("tags","Schlagwörter")] %}
    <form method="post" class="mb-3"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><input type="hidden" name="action" value="{{t}}"><label class="form-label">{{label}} (durch Komma getrennt)</label><div class="input-group"><input class="form-control" name="names" value="{{terms[t]}}"><button class="btn btn-outline-secondary">Speichern</button></div></form>{% endfor %}
    </div><div class="col-lg-5"><h2 class="h5">Cover und Exemplare</h2>
    {% if book.cover_url %}<img src="{{book.cover_url}}" alt="Cover" class="img-thumbnail mb-2" style="max-height:190px">
      <div class="small text-muted mb-2">{{book.cover_source or 'Vorhandenes Cover'}}{% if book.cover_confidence %} · {{book.cover_confidence}} %{% endif %}</div>
      <form method="post" class="mb-3"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><input type="hidden" name="action" value="remove_cover"><button class="btn btn-outline-danger btn-sm">Falsches Cover entfernen</button></form>{% endif %}
    {% if not book.cover_url %}<a class="btn btn-outline-primary btn-sm mb-3" href="{{url_for('catalog_extra.detail',book_id=book.id,cover_search=1)}}">Erweiterte Cover-Suche starten</a>{% endif %}
    <form method="post" enctype="multipart/form-data" action="{{url_for('catalog_extra.upload_cover',book_id=book.id)}}" class="mb-3">
      <input type="hidden" name="csrf_token" value="{{session.csrf_token}}">
      <label class="form-label small">Eigenes Cover (JPG, PNG oder WebP)</label>
      <div class="input-group input-group-sm"><input class="form-control" type="file" name="cover_file" accept="image/jpeg,image/png,image/webp" required><button class="btn btn-outline-primary">Hochladen</button></div>
    </form>
    {% if request.args.get('cover_search')=='1' and not book.cover_url %}
      <p class="small text-muted">Vorschläge sind nicht automatisch geprüft. Bitte Titel, Autor und Ausgabe mit dem echten Buch vergleichen.</p>
      {% if not suggestions %}<p>Keine passenden Cover-Vorschläge gefunden.</p>{% endif %}
      {% for item in suggestions %}
        <div class="border rounded p-2 mb-2"><img src="{{item.cover_url}}" alt="Vorgeschlagenes Cover" style="max-height:110px;max-width:85px;object-fit:contain">
        <div class="small"><strong>{{item.title}}</strong><div>{{item.authors}}</div><div>{{item.source}} · ISBN {{item.isbn or 'unbekannt'}} · Treffer {{item.confidence}} %</div></div>
        <form method="post"><input type="hidden" name="csrf_token" value="{{session.csrf_token}}"><input type="hidden" name="action" value="choose_cover"><input type="hidden" name="cover_url" value="{{item.cover_url}}"><button class="btn btn-outline-primary btn-sm mt-2">Dieses Cover übernehmen</button></form></div>
      {% endfor %}
    {% endif %}
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
    <button class="btn btn-primary d-block">Exemplar hinzufügen</button></form></details></div></div>''',book=book,copies=copies,terms=terms,suggestions=suggestions)
