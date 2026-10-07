"""Usability and workflow regressions for the consolidated library UI."""
import os
import tempfile
import unittest

import app as module


class UsabilityWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_database=module.DATABASE
        self.old_upload_dir=module.UPLOAD_DIR
        module.DATABASE=os.path.join(self.tmp.name,'test.db')
        module.UPLOAD_DIR=os.path.join(self.tmp.name,'uploads')
        module.init_db()
        with module.app.app_context():
            db=module.get_db()
            db.execute("INSERT INTO users(username,full_name,role,is_active) VALUES('member','Max Mitglied','user',1)")
            self.member_id=db.execute("SELECT id FROM users WHERE username='member'").fetchone()[0]
            db.execute("INSERT INTO users(username,full_name,role,is_active) VALUES('lib','Berta Bibliothekar','librarian',1)")
            self.lib_id=db.execute("SELECT id FROM users WHERE username='lib'").fetchone()[0]
            db.execute("""INSERT INTO books(title,author,category,description,total_copies,published)
                          VALUES('Allgäu Test','Anna Autor','Wandern',
                          '{"Gebietsthema":"Allgäu","Sachthema":"Bergwandern"}',1,'2025')""")
            self.book_id=db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("""INSERT INTO book_copies(book_id,copy_number,inventory_code,barcode,qr_token)
                          VALUES(?,1,'LG-TEST-1','123456789','token-usability')""",(self.book_id,))
            db.execute("""INSERT INTO books(title,author,category,total_copies)
                          VALUES('Anderes Buch','B Autor','Klettern',1)""")
            other=db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("""INSERT INTO book_copies(book_id,copy_number,inventory_code,qr_token)
                          VALUES(?,1,'LG-OTHER-1','token-other')""",(other,))
            db.commit()
        self.client=module.app.test_client()

    def tearDown(self):
        module.DATABASE=self.old_database
        module.UPLOAD_DIR=self.old_upload_dir
        self.tmp.cleanup()

    def login(self,user_id,role,name='Test'):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess.update(user_id=user_id,role=role,full_name=name,username='test',
                        csrf_token='csrf',must_change_password=False,lang='de')

    def post(self,path,**data):
        return self.client.post(path,data={'csrf_token':'csrf',**data})

    def test_catalog_filters_and_readable_date(self):
        self.login(self.member_id,'user','Max Mitglied')
        response=self.client.get('/catalog?category=Wandern&area=Allg%C3%A4u&topic=Bergwandern&available=1')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Allg\xc3\xa4u Test',response.data)
        self.assertNotIn(b'Anderes Buch',response.data)
        self.assertIn(b'1 Medium gefunden',response.data)
        self.assertEqual(module.de_date('2026-11-04'),'04.11.2026')

    def test_librarian_lands_on_workbench_and_can_scan_checkout_return(self):
        self.login(self.lib_id,'librarian','Berta Bibliothekar')
        root=self.client.get('/')
        self.assertEqual(root.status_code,302)
        self.assertTrue(root.headers['Location'].endswith('/admin/loans'))
        page=self.client.get('/admin/loans')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'Direkte Ausgabe',page.data)
        self.assertIn(b'Schnelle R\xc3\xbccknahme',page.data)
        self.assertNotIn(b'Phase 3',page.data)

        response=self.post('/admin/loans-extra/borrow',
                           copy_lookup='LG-TEST-1',
                           member_lookup='member \xe2\x80\x93 Max Mitglied')
        self.assertEqual(response.status_code,302)
        with module.app.app_context():
            loan=module.get_db().execute("SELECT * FROM loans WHERE book_id=?",(self.book_id,)).fetchone()
            self.assertEqual(loan['status'],'borrowed')
            self.assertIsNotNone(loan['copy_id'])

        response=self.post('/admin/loans-extra/quick-return',return_code='123456789')
        self.assertEqual(response.status_code,302)
        with module.app.app_context():
            status=module.get_db().execute("SELECT status FROM loans WHERE book_id=?",(self.book_id,)).fetchone()[0]
            self.assertEqual(status,'returned')

    def test_member_card_requests_renewal(self):
        self.login(self.lib_id,'librarian','Berta Bibliothekar')
        self.post('/admin/loans-extra/borrow',
                  copy_lookup='LG-TEST-1',
                  member_lookup='member \xe2\x80\x93 Max Mitglied')
        with module.app.app_context():
            loan_id=module.get_db().execute("SELECT id FROM loans WHERE book_id=?",(self.book_id,)).fetchone()[0]

        self.login(self.member_id,'user','Max Mitglied')
        page=self.client.get('/my-loans')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'Verl\xc3\xa4ngerung beantragen',page.data)
        response=self.post(f'/my-loans/{loan_id}/request-renewal')
        self.assertEqual(response.status_code,302)
        page=self.client.get('/my-loans')
        self.assertIn(b'wartet auf Bibliothek',page.data)


if __name__=='__main__':
    unittest.main()
