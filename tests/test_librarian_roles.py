"""Role and circulation workflow regressions. Uses an isolated temporary database."""
import os
import tempfile
import unittest
from unittest.mock import patch

import app as module


class LibrarianWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_database = module.DATABASE
        module.DATABASE = os.path.join(self.tmp.name, 'test.db')
        module.init_db()
        with module.app.app_context():
            db = module.get_db()
            db.execute("INSERT INTO users(username,full_name,role) VALUES ('member','Mitglied','user')")
            self.member_id = db.execute("SELECT id FROM users WHERE username='member'").fetchone()[0]
            db.execute("INSERT INTO users(username,full_name,role) VALUES ('librarian','Bibliothekar','librarian')")
            self.librarian_id = db.execute("SELECT id FROM users WHERE username='librarian'").fetchone()[0]
            db.execute("INSERT INTO books(title,author,total_copies) VALUES ('Testbuch','Testautor',1)")
            self.book_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT INTO book_copies(book_id,copy_number,inventory_code,qr_token) VALUES (?,1,'Test-1','token-1')", (self.book_id,))
            db.commit()
            self.admin_id = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
        self.client = module.app.test_client()

    def tearDown(self):
        module.DATABASE = self.old_database
        self.tmp.cleanup()

    def login(self, user_id, role):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess.update(user_id=user_id,role=role,csrf_token='test-csrf',
                        must_change_password=False)

    def post(self, path, **data):
        return self.client.post(path, data={'csrf_token':'test-csrf',**data})

    def state(self):
        with module.app.app_context():
            db = module.get_db()
            return dict(db.execute('SELECT * FROM loans LIMIT 1').fetchone())

    def test_role_separation_and_circulation(self):
        self.login(self.member_id,'user')
        r=self.post(f'/book/{self.book_id}/reserve')
        self.assertEqual(r.status_code,302)
        self.assertEqual(self.state()['status'],'reserved')
        self.assertEqual(self.client.get('/admin/loans').status_code,403)
        self.assertEqual(self.client.get('/admin/loans-extra/').status_code,403)
        self.assertEqual(self.post(f'/book/{self.book_id}/return').status_code,403)
        self.assertEqual(self.state()['status'],'reserved')

        self.login(self.librarian_id,'librarian')
        self.assertEqual(self.client.get('/admin/loans').status_code,200)
        self.assertEqual(self.client.get('/admin/loans-extra/').status_code,200)
        self.assertEqual(self.client.get('/admin/users').status_code,403)
        self.assertEqual(self.post('/admin/loans-extra/settings',days='5',max='4').status_code,403)
        loan = self.state()
        self.assertEqual(self.post(f"/admin/loans/{loan['id']}/confirm").status_code,302)
        loan=self.state()
        self.assertEqual(loan['status'],'borrowed')
        self.assertIsNotNone(loan['copy_id'])
        self.assertEqual(self.post(f"/admin/loans-extra/{loan['id']}/action",action='renew').status_code,302)
        self.assertEqual(self.state()['renewal_count'],0)

        self.login(self.member_id,'user')
        self.assertEqual(self.post(f"/my-loans/{loan['id']}/request-renewal").status_code,302)
        self.assertIsNotNone(self.state()['renewal_requested_at'])
        self.assertEqual(self.post(f'/book/{self.book_id}/return').status_code,403)

        self.login(self.librarian_id,'librarian')
        self.assertEqual(self.post(f"/admin/loans-extra/{loan['id']}/action",action='renew').status_code,302)
        self.assertEqual(self.state()['renewal_count'],1)
        self.assertIsNone(self.state()['renewal_requested_at'])
        self.assertEqual(self.post(f"/admin/loans-extra/{loan['id']}/action",action='return').status_code,302)
        self.assertEqual(self.state()['status'],'returned')

    def test_only_admin_can_assign_librarian_role(self):
        self.login(self.member_id,'user')
        self.assertEqual(self.client.get('/admin/users').status_code,403)
        self.login(self.admin_id,'admin')
        response=self.post('/admin/users/add',username='newlib',full_name='Neue Bibliothekarin',role='librarian')
        self.assertEqual(response.status_code,302)
        with module.app.app_context():
            self.assertEqual(module.get_db().execute("SELECT role FROM users WHERE username='newlib'").fetchone()[0],'librarian')


if __name__ == '__main__':
    unittest.main()
