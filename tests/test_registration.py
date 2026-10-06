import unittest
import tempfile
import os
from werkzeug.security import generate_password_hash

class RegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        os.environ['DATABASE']=os.path.join(cls.tmp.name,'db.sqlite')
        os.environ['UPLOAD_DIR']=os.path.join(cls.tmp.name,'uploads')
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

    def test_existing_session_revoked_after_lock(self):
        from app import get_db
        from werkzeug.security import generate_password_hash
        c=self.app.test_client()
        with self.app.app_context():
            db=get_db()
            db.execute("""INSERT INTO users(username,password_hash,full_name,email,role,is_active,must_change_password)
                          VALUES(?,?,?,?, 'user',1,0)""",
                       ('session_member',generate_password_hash('Strong!Password123'),
                        'Session Member','session@example.org'))
            db.commit()
            user_id=db.execute("SELECT id FROM users WHERE username='session_member'").fetchone()[0]
        with c.session_transaction() as sess:
            sess['user_id']=user_id
            sess['role']='user'
            sess['username']='session_member'
        self.assertEqual(c.get('/catalog').status_code,200)
        with self.app.app_context():
            db=get_db()
            db.execute("UPDATE users SET is_active=0 WHERE id=?", (user_id,))
            db.commit()
        denied=c.get('/catalog',follow_redirects=False)
        self.assertEqual(denied.status_code,302)
        self.assertIn('/login',denied.headers['Location'])
        with c.session_transaction() as sess:
            self.assertNotIn('user_id',sess)

    def test_registration_failed_attempts_are_limited(self):
        from app import _login_attempts
        c=self.app.test_client()
        for _ in range(5):
            c.get('/register')
            with c.session_transaction() as sess:
                token=sess['csrf_token']
            c.post('/register',data={'csrf_token':token,'captcha':'999'})
        self.assertEqual(len(_login_attempts['register:127.0.0.1']),5)
        c.get('/register')
        with c.session_transaction() as sess:
            token=sess['csrf_token']
            answer=sess['captcha_ans']
        blocked=c.post('/register',data={'csrf_token':token,'captcha':str(answer),
            'username':'limitedmember','full_name':'Limited Member',
            'email':'limited@example.org','password':'Strong!Password123',
            'password_confirm':'Strong!Password123'})
        self.assertEqual(blocked.status_code,200)
        self.assertIn('Zu viele Versuche',blocked.get_data(as_text=True))
        _login_attempts.pop('register:127.0.0.1',None)
