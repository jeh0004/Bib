import io
import sqlite3
import unittest
from openpyxl import Workbook
from book_import import parse_xlsx, import_rows, HEADERS

class BookImportTests(unittest.TestCase):
    def make_xlsx(self, duplicate=False):
        w = Workbook()
        s = w.active
        s.append(HEADERS)
        s.append([3905,"Autor","Titel","Buch","Verlag","Gebiet","Sach",2006,"B1","978-1",2,None,None,None])
        if duplicate:
            s.append([3905,"Autor","Titel","Buch",None,None,None,None,None,None,None,None,None,None])
        f=io.BytesIO();w.save(f)
        return f.getvalue()
    def db(self):
        db=sqlite3.connect(":memory:")
        db.executescript("""
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL, author TEXT NOT NULL,
        isbn TEXT, category TEXT, description TEXT, total_copies INTEGER);
        CREATE TABLE book_copies (id INTEGER PRIMARY KEY,book_id INTEGER,copy_number INTEGER,
        inventory_code TEXT UNIQUE,qr_token TEXT UNIQUE,location TEXT,notes TEXT);
        """)
        return db
    def test_preview_import_and_idempotency(self):
        rows,errors=parse_xlsx(self.make_xlsx())
        db=self.db()
        self.assertEqual(import_rows(db,rows,errors)["new"],1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM books").fetchone()[0],0)
        self.assertTrue(import_rows(db,rows,errors,True)["saved"])
        self.assertEqual(import_rows(db,rows,errors,True)["skipped"],1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM books").fetchone()[0],1)
        self.assertEqual(db.execute("SELECT inventory_code FROM book_copies").fetchone()[0],"3905")
    def test_duplicates_block_all_writes(self):
        rows,errors=parse_xlsx(self.make_xlsx(True))
        self.assertTrue(errors)
        db=self.db()
        self.assertFalse(import_rows(db,rows,errors,True)["saved"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM books").fetchone()[0],0)
