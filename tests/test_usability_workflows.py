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
            db.execute("""INSERT INTO books(title,author,category,description,total_copies,published,area,topic)
                          VALUES('Allgäu Test','Anna Autor','Wandern',
                          '{"Gebietsthema":"Allgäu","Sachthema":"Bergwandern"}',1,'2025','Allgäu','Bergwandern')""")
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
                           member_lookup='member')
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
                  member_lookup='member')
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

    def test_catalog_pagination_sorting_and_back_link(self):
        self.login(self.member_id,'user','Max Mitglied')
        with module.app.app_context():
            db=module.get_db()
            for i in range(30):
                db.execute("INSERT INTO books(title,author,total_copies) VALUES(?,?,1)",(f'Zusatztitel {i:02d}','Autor'))
                book_id=db.execute("SELECT last_insert_rowid()").fetchone()[0]
                db.execute("INSERT INTO book_copies(book_id,copy_number,inventory_code,qr_token) VALUES(?,1,?,?)",
                           (book_id,f'EXTRA-{i}',f'token-{i}'))
            db.commit()
        page=self.client.get('/catalog?sort=author&page=2')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'pagination',page.data)
        detail_url=f'/book/{self.book_id}?back=%2Fcatalog%3Fcategory%3DWandern%26available%3D1'
        detail=self.client.get(detail_url)
        self.assertEqual(detail.status_code,200)
        self.assertIn(b'/catalog?category=Wandern&amp;available=1',detail.data)

    def test_reservation_expiry_waitlist_promotion_and_audit(self):
        with module.app.app_context():
            db=module.get_db()
            db.execute("UPDATE loan_policy SET reservation_days=3 WHERE id=1")
            db.commit()
        self.login(self.member_id,'user','Max Mitglied')
        self.post(f'/book/{self.book_id}/reserve')
        with module.app.app_context():
            db=module.get_db()
            loan=db.execute("SELECT * FROM loans WHERE book_id=? AND status='reserved'",(self.book_id,)).fetchone()
            self.assertIsNotNone(loan['reservation_expires_at'])
            db.execute("UPDATE loans SET reservation_expires_at=datetime('now','-1 day') WHERE id=?",(loan['id'],))
            db.commit()
            self.assertEqual(module.expire_reservations(db),1)
            self.assertEqual(db.execute("SELECT status FROM loans WHERE id=?",(loan['id'],)).fetchone()[0],'cancelled')
            self.assertEqual(db.execute("SELECT action FROM circulation_audit WHERE loan_id=? ORDER BY id DESC",(loan['id'],)).fetchone()[0],'reservation_expired')

            db.execute("INSERT INTO loan_waitlist(book_id,user_id) VALUES(?,?)",(self.book_id,self.member_id))
            db.commit()
        self.login(self.lib_id,'librarian','Berta Bibliothekar')
        page=self.client.get('/admin/loans')
        self.assertIn(b'Warteliste',page.data)
        response=self.post(f'/admin/waitlist/{self.book_id}/promote')
        self.assertEqual(response.status_code,302)
        with module.app.app_context():
            db=module.get_db()
            self.assertIsNotNone(db.execute("SELECT 1 FROM loans WHERE book_id=? AND status='reserved'",(self.book_id,)).fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM loan_waitlist WHERE book_id=?",(self.book_id,)).fetchone())
            self.assertEqual(db.execute("SELECT action FROM circulation_audit ORDER BY id DESC LIMIT 1").fetchone()[0],'waitlist_promoted')

    def test_renewal_blocked_when_someone_waits_and_overdue_is_visible(self):
        self.login(self.lib_id,'librarian','Berta Bibliothekar')
        self.post('/admin/loans-extra/borrow',copy_lookup='LG-TEST-1',member_lookup='member')
        with module.app.app_context():
            db=module.get_db()
            loan=db.execute("SELECT * FROM loans WHERE book_id=? AND status='borrowed'",(self.book_id,)).fetchone()
            db.execute("INSERT INTO users(username,full_name,role,is_active) VALUES('waiting','Wartendes Mitglied','user',1)")
            waiting_id=db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT INTO loan_waitlist(book_id,user_id) VALUES(?,?)",(self.book_id,waiting_id))
            db.execute("UPDATE loans SET due_date=date('now','-3 days') WHERE id=?",(loan['id'],))
            db.commit()
            loan_id=loan['id']
        self.login(self.member_id,'user','Max Mitglied')
        page=self.client.get('/my-loans')
        self.assertIn(b'Verl\xc3\xa4ngerung derzeit nicht m\xc3\xb6glich',page.data)
        self.assertIn(b'\xc3\x9cberf\xc3\xa4llig seit 3 Tagen',page.data)
        self.post(f'/my-loans/{loan_id}/request-renewal')
        with module.app.app_context():
            self.assertIsNone(module.get_db().execute("SELECT renewal_requested_at FROM loans WHERE id=?",(loan_id,)).fetchone()[0])

    def test_checkout_rejects_staff_as_borrower_and_legacy_page_redirects(self):
        from loans_extra import checkout
        self.login(self.lib_id,'librarian','Berta Bibliothekar')
        with module.app.app_context():
            db=module.get_db()
            copy_id=db.execute("SELECT id FROM book_copies WHERE inventory_code='LG-TEST-1'").fetchone()[0]
            with self.assertRaises(ValueError):
                checkout(db,copy_id,self.lib_id)
        response=self.client.get('/admin/loans-extra/')
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.headers['Location'].endswith('/admin/loans'))

    def test_audit_logs_checkout_and_return(self):
        self.login(self.lib_id,'librarian','Berta Bibliothekar')
        self.post('/admin/loans-extra/borrow',copy_lookup='LG-TEST-1',member_lookup='member')
        self.post('/admin/loans-extra/quick-return',return_code='123456789')
        with module.app.app_context():
            actions=[r[0] for r in module.get_db().execute("SELECT action FROM circulation_audit ORDER BY id").fetchall()]
            self.assertEqual(actions,['checkout','return'])

    def test_german_is_forced_for_existing_english_session(self):
        self.login(self.member_id,'user','Max Mitglied')
        with self.client.session_transaction() as sess:
            sess['lang']='en'
        page=self.client.get('/catalog')
        self.assertEqual(page.status_code,200)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess['lang'],'de')
        self.assertIn(b'B\xc3\xbccherkatalog',page.data)

    def test_structured_metadata_is_used(self):
        self.login(self.member_id,'user','Max Mitglied')
        with module.app.app_context():
            from migrations.catalog import migrate as migrate_catalog
            db=module.get_db()
            migrate_catalog(db)
            book=db.execute("SELECT area,topic FROM books WHERE id=?",(self.book_id,)).fetchone()
            self.assertEqual(book['area'],'Allgäu')
            self.assertEqual(book['topic'],'Bergwandern')
        result=self.client.get('/catalog?area=Allg%C3%A4u&topic=Bergwandern')
        self.assertIn(b'Allg\xc3\xa4u Test',result.data)


if __name__=='__main__':
    unittest.main()
