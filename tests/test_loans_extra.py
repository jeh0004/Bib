import sqlite3, tempfile, unittest
from pathlib import Path
from datetime import date
from migrations.catalog import migrate as catalog_migrate
from migrations.loans import migrate as loan_migrate
import sys, types
try:
 import flask
except ImportError:
 fake=types.ModuleType('flask')
 fake.Blueprint=lambda *a,**k: type('BlueprintStub',(),{'route':lambda self,*a,**k:lambda fn:fn,'post':lambda self,*a,**k:lambda fn:fn})()
 for name in ('abort','flash','redirect','render_template_string','url_for'):
  setattr(fake,name,lambda *a,**k:None)
 fake.request=None;fake.session=None
 sys.modules['flask']=fake
from loans_extra import checkout, renew

ROOT=Path(__file__).resolve().parents[1]
class LoanTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
  self.db.executescript((ROOT/'tests'/'schema_fixture.sql').read_text())
  self.db.execute("INSERT INTO users(username,full_name) VALUES('alice','Alice')")
  self.db.execute("INSERT INTO users(username,full_name) VALUES('bob','Bob')")
  self.db.execute("INSERT INTO books(title,author,total_copies) VALUES('B','A',2)")
  self.db.commit();catalog_migrate(self.db);loan_migrate(self.db)
 def tearDown(self): self.db.close()
 def test_checkout_and_renewal(self):
  due=checkout(self.db,1,1)
  self.assertGreater(date.fromisoformat(due),date.today())
  self.assertGreater(date.fromisoformat(renew(self.db,1)),date.fromisoformat(due))
  renew(self.db,1)
  with self.assertRaises(ValueError):renew(self.db,1)
 def test_copy_not_double_loaned(self):
  checkout(self.db,1,1)
  with self.assertRaises(ValueError):checkout(self.db,1,2)
  checkout(self.db,2,2)
 def test_legacy_reservations_consume_capacity(self):
  self.db.execute("INSERT INTO loans(book_id,user_id,status) VALUES(1,1,'reserved')")
  self.db.commit()
  checkout(self.db,2,2)
  with self.assertRaises(ValueError):checkout(self.db,1,2)
 def test_unassigned_legacy_loan_blocks_remaining_physical_copy(self):
  self.db.execute("INSERT INTO loans(book_id,user_id,status) VALUES(1,1,'borrowed')")
  self.db.commit()
  checkout(self.db,2,2)
  with self.assertRaises(ValueError):checkout(self.db,1,1)
 def test_invalid_and_inactive_users(self):
  with self.assertRaises(ValueError):checkout(self.db,1,999)
  self.db.execute("UPDATE users SET is_active=0 WHERE id=1")
  self.db.commit()
  with self.assertRaises(ValueError):checkout(self.db,1,1)
 def test_migration_preserves_existing_loans(self):
  self.db.execute("INSERT INTO loans(book_id,user_id,status) VALUES(1,1,'borrowed')")
  self.db.commit()
  catalog_migrate(self.db);loan_migrate(self.db)
  self.assertEqual(self.db.execute("SELECT status FROM loans").fetchone()[0],"borrowed")
 def test_repeat_migrations(self):
  catalog_migrate(self.db);loan_migrate(self.db)
  self.assertEqual(self.db.execute('SELECT count(*) FROM book_copies').fetchone()[0],2)
if __name__=='__main__': unittest.main()
