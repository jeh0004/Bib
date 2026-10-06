import unittest
import tempfile
import os
from werkzeug.security import generate_password_hash

class RegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        os.environ['DATABASE']=os.path.join(cls.tmp.name,'db.sqlite')
        from app import app, init_db
        cls.app=app
        init_db()
    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
    def test_registration_requires_admin_approval(self):
        from app import get_db
        c=self.app.test_client()
        page=c.get('/register')
        self.assertEqual(page.status_code,200)
        with c.session_transaction() as s:
            captcha=s['captcha_ans']
            csrf=s['csrf_token']
        result=c.post('/register',data={'username':'newmember','full_name':'Test Member',
            'email':'member@example.org','password':'Strong!Password123',
            'password_confirm':'Strong!Password123','captcha':captcha,'csrf_token':csrf})
        self.assertEqual(result.status_code,302)
        with self.app.app_context():
            row=get_db().execute("SELECT role,is_active FROM users WHERE username='newmember'").fetchone()
            self.assertEqual(tuple(row),('user',0))
        page=c.get('/login')
        with c.session_transaction() as s:
            captcha=s['captcha_ans']
            csrf=s['csrf_token']
        denied=c.post('/login',data={'username':'newmember','password':'Strong!Password123',
            'captcha':captcha,'csrf_token':csrf},follow_redirects=False)
        self.assertEqual(denied.status_code,200)
