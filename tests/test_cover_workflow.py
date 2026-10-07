import io
import os
import tempfile
import unittest
from unittest.mock import patch

import app as module


class CoverWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_database=module.DATABASE
        self.old_upload_dir=module.UPLOAD_DIR
        module.DATABASE=os.path.join(self.tmp.name,'cover.db')
        module.UPLOAD_DIR=os.path.join(self.tmp.name,'uploads')
        module.init_db()
        with module.app.app_context():
            db=module.get_db()
            db.execute("INSERT INTO users(username,full_name,role,is_active) VALUES('coveradmin','Cover Admin','admin',1)")
            self.admin_id=db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("""INSERT INTO books(title,author,isbn,publisher,total_copies)
                          VALUES('Kaiser Test','Anna Autor','9781234567897','Test Verlag',1)""")
            self.book_id=db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()
        self.client=module.app.test_client()
        with self.client.session_transaction() as sess:
            sess.update(user_id=self.admin_id,role='admin',full_name='Cover Admin',
                        username='coveradmin',csrf_token='csrf',must_change_password=False,lang='de')

    def tearDown(self):
        module.DATABASE=self.old_database
        module.UPLOAD_DIR=self.old_upload_dir
        self.tmp.cleanup()

    def post(self,path,data=None,content_type=None):
        payload={'csrf_token':'csrf'}
        if data: payload.update(data)
        return self.client.post(path,data=payload,content_type=content_type)

    def test_high_confidence_exact_isbn_cover_is_applied_automatically(self):
        candidate=dict(source='Open Library',title='Kaiser Test',authors='Anna Autor',
                       cover_url='https://covers.openlibrary.org/b/id/123-L.jpg',
                       isbn='9781234567897',publisher='Test Verlag',
                       confidence=100,exact_isbn=True,query='ISBN')
        with patch('catalog_extra.expanded_cover_candidates',return_value=[candidate]):
            response=self.post('/admin/catalog-extra/covers/scan')
        self.assertEqual(response.status_code,302)
        with module.app.app_context():
            row=module.get_db().execute(
                "SELECT cover_url,cover_status,cover_source,cover_confidence FROM books WHERE id=?",
                (self.book_id,)).fetchone()
            self.assertEqual(row['cover_status'],'auto')
            self.assertEqual(row['cover_source'],'Open Library')
            self.assertEqual(row['cover_confidence'],100)
            self.assertIn('123-L.jpg',row['cover_url'])

    def test_ambiguous_candidates_are_presented_for_review_and_can_be_chosen(self):
        candidate=dict(source='Google Books',title='Kaiser Test',authors='Anna Autor',
                       cover_url='https://books.google.com/cover-test.jpg',
                       isbn='',publisher='Test Verlag',
                       confidence=34,exact_isbn=False,query='Titel+Autor')
        with patch('catalog_extra.expanded_cover_candidates',return_value=[candidate]):
            self.post('/admin/catalog-extra/covers/scan')
        page=self.client.get('/admin/catalog-extra/covers/review')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'Cover pr\xc3\xbcfen',page.data)
        self.assertIn(b'Google Books',page.data)
        with module.app.app_context():
            db=module.get_db()
            candidate_id=db.execute(
                "SELECT id FROM cover_candidates WHERE book_id=? AND status='pending'",
                (self.book_id,)).fetchone()[0]
        response=self.post(f'/admin/catalog-extra/covers/{self.book_id}/choose/{candidate_id}')
        self.assertEqual(response.status_code,302)
        with module.app.app_context():
            row=module.get_db().execute(
                "SELECT cover_status,cover_source FROM books WHERE id=?",(self.book_id,)).fetchone()
            self.assertEqual(row['cover_status'],'reviewed')
            self.assertEqual(row['cover_source'],'Google Books')

    def test_verified_isbn10_specialist_fallback_is_used(self):
        from catalog_extra import specialist_cover_candidates
        with module.app.app_context():
            db=module.get_db()
            db.execute("""UPDATE books SET title='Alpinführer Bündner Alpen 4 südliches Bergell Disgrazia',
                          author='Meier',isbn='3859022520',publisher='SAC Verlag' WHERE id=?""",(self.book_id,))
            db.commit()
            book=db.execute("SELECT * FROM books WHERE id=?",(self.book_id,)).fetchone()
        fake=dict(source='ZVAB',title='Clubführer Bündner Alpen 4',authors='Meier',
                  cover_url='https://example.org/cover.jpg',isbn='3859022520',
                  publisher='SAC',confidence=94,exact_isbn=True,query='Spezialquelle',
                  auto_eligible=False,product_url='https://www.zvab.com/example')
        with patch('catalog_extra._specialist_product',return_value=fake) as mocked:
            result=specialist_cover_candidates(book)
        self.assertTrue(result)
        self.assertFalse(result[0]['auto_eligible'])
        self.assertTrue(any('9783859022522' in call.args[0] for call in mocked.call_args_list))

    def test_specialist_exact_isbn_candidate_stays_manual_review(self):
        candidate=dict(source='Das Landkartenhaus',title='Kaiser Test',authors='Anna Autor',
                       cover_url='https://www.das-landkartenhaus.de/media/cover.jpg',
                       isbn='9781234567897',publisher='Test Verlag',
                       confidence=94,exact_isbn=True,query='Spezialquelle',
                       auto_eligible=False,
                       product_url='https://www.das-landkartenhaus.de/kaiser-test')
        with patch('catalog_extra.expanded_cover_candidates',return_value=[candidate]):
            response=self.post('/admin/catalog-extra/covers/scan')
        self.assertEqual(response.status_code,302)
        with module.app.app_context():
            db=module.get_db()
            book=db.execute("SELECT cover_url,cover_status FROM books WHERE id=?",(self.book_id,)).fetchone()
            self.assertIsNone(book['cover_url'])
            self.assertEqual(book['cover_status'],'review')
            row=db.execute("""SELECT source,source_url,status FROM cover_candidates
                              WHERE book_id=?""",(self.book_id,)).fetchone()
            self.assertEqual(row['source'],'Das Landkartenhaus')
            self.assertEqual(row['source_url'],'https://www.das-landkartenhaus.de/kaiser-test')
            self.assertEqual(row['status'],'pending')

    def test_manual_cover_upload_is_stored_locally(self):
        # Minimal JPEG signature is sufficient for server-side format validation.
        image=io.BytesIO(b'\xff\xd8\xff\xe0'+b'0'*64)
        response=self.client.post(
            f'/admin/catalog-extra/covers/{self.book_id}/upload',
            data={'csrf_token':'csrf','cover_file':(image,'cover.jpg')},
            content_type='multipart/form-data')
        self.assertEqual(response.status_code,302)
        with module.app.app_context():
            row=module.get_db().execute(
                "SELECT cover_url,cover_status,cover_source,cover_confidence FROM books WHERE id=?",
                (self.book_id,)).fetchone()
            self.assertEqual(row['cover_status'],'manual')
            self.assertEqual(row['cover_source'],'Manueller Upload')
            self.assertEqual(row['cover_confidence'],100)
            self.assertTrue(row['cover_url'].startswith('/uploads/book-cover-'))
            filename=row['cover_url'].split('/')[-1]
            self.assertTrue(os.path.exists(os.path.join(module.UPLOAD_DIR,filename)))

    def test_cover_schema_migration_is_idempotent(self):
        from migrations.catalog import migrate
        with module.app.app_context():
            db=module.get_db()
            migrate(db)
            migrate(db)
            columns={r[1] for r in db.execute("PRAGMA table_info(books)")}
            self.assertTrue({'cover_source','cover_status','cover_confidence','cover_checked_at'} <= columns)
            self.assertIsNotNone(db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cover_candidates'"
            ).fetchone())
            candidate_columns={r[1] for r in db.execute("PRAGMA table_info(cover_candidates)")}
            self.assertIn('source_url',candidate_columns)


if __name__=='__main__':
    unittest.main()
