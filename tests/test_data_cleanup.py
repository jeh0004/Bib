import json
import sqlite3
import unittest

from migrations.data_cleanup import migrate, VERSION
from migrations.data_cleanup_v2 import migrate as migrate_v2, VERSION as VERSION_V2


class CatalogCleanupTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute("""CREATE TABLE books(
            id INTEGER PRIMARY KEY, title TEXT, author TEXT, isbn TEXT, category TEXT,
            publisher TEXT, published TEXT, area TEXT, topic TEXT, book_index TEXT,
            description TEXT
        )""")
        self.db.executemany("""INSERT INTO books
            (id,title,author,isbn,category,publisher,published,area,topic,book_index,description)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",[
            (4,'Alpin Lehrplan 1\u00a0  Bergwandern Trekking','DAV','978-3-7633-6109-0',
             'Alpin Lehrplan','Rother','2024','Alpen','Bergwandern Trekking','L 4',
             json.dumps({'Auflagedatum':'2024'},ensure_ascii=False)),
            (30,'Allgäuer-Lechtaler Alpen Westlatt Nr. 2/1\u00a0 1:25000','Unbekannt','978-3-928777-13-1',
             'AV-Karte','Alpenverein','2019','Allgäu-Lechtal','Wandern, Bergsteigen','KNK 3 a',
             json.dumps({'Auflagedatum':'2019'},ensure_ascii=False)),
            (42,'Pfälzer Burgenbrevier','Unbekannt','keine','Burgen-Führer','W\\.Hartung','1969',
             'Pfalz','Burgen','Fn 5',
             '{"Verlag": "W\\\\.Hartung", "Auflagedatum": "1969"}'),
            (68,'Alpen  100 Touren Highlights','Unbekannt','978-3-7633-3207-6',
             'Jubiläums Wanderführer','Rother','January 1, 2020','Alpen','Waqndern','FZ 103',
             json.dumps({'Auflagedatum':'2020'},ensure_ascii=False)),
            (219,'Die schönsten Loipen zwischen Allgäu und Berchtesgaden','Unbekannt','967-3-7654-5149-2',
             'Skilanglaufführer','Bruckmann','2008','Allgäu Berchtesgaden','Skilanglauf','SWN 4',
             json.dumps({'Auflagedatum':'2008'},ensure_ascii=False)),
            (220,'Südtirol Band 1 Pustertal und nördliche Dolomiten','Piepenstock','978-3-95611-112-9',
             'Skitorenführer','Panico Alpinverlag','2020','Pustertal Dolomiten','Skitouren','ST 28',
             json.dumps({'Auflagedatum':'2020'},ensure_ascii=False)),
            (675,'Dolomites UNESCO Geotrail','Ladurner','9788870739015',
             'Wanderführer+2x Karten','Tappeiner','2018','Dolomiten','Wanern','FSKD 48',
             json.dumps({'Auflagedatum':'2018'},ensure_ascii=False)),
            (681,'Der Friedenspfad Sentiero della Pace Im Trentino','Unbekannt','3930187094',
             'Weitwnderführer','Weitwanderverla','1997','Trentino','Weitwandern','FSKD 12',
             json.dumps({'Auflagedatum':'1997'},ensure_ascii=False)),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_cleanup_corrects_high_confidence_catalog_errors(self):
        changed=migrate(self.db)
        self.assertEqual(changed,8)

        row=self.db.execute("SELECT * FROM books WHERE id=4").fetchone()
        self.assertEqual(row['category'],'Alpin-Lehrplan')
        self.assertEqual(row['title'],'Alpin Lehrplan 1 Bergwandern Trekking')

        row=self.db.execute("SELECT title FROM books WHERE id=30").fetchone()
        self.assertIn('Westblatt',row['title'])

        row=self.db.execute("SELECT * FROM books WHERE id=42").fetchone()
        self.assertIsNone(row['isbn'])
        self.assertEqual(row['category'],'Burgenführer')
        self.assertEqual(row['publisher'],'W. Hartung')
        self.assertIsInstance(json.loads(row['description']),dict)

        row=self.db.execute("SELECT published,topic,category FROM books WHERE id=68").fetchone()
        self.assertEqual(row['published'],'2020')
        self.assertEqual(row['topic'],'Wandern')
        self.assertEqual(row['category'],'Jubiläums-Wanderführer')

        self.assertEqual(self.db.execute("SELECT isbn FROM books WHERE id=219").fetchone()[0],
                         '978-3-7654-5149-2')
        self.assertEqual(self.db.execute("SELECT category FROM books WHERE id=220").fetchone()[0],
                         'Skitourenführer')
        self.assertEqual(self.db.execute("SELECT topic FROM books WHERE id=675").fetchone()[0],
                         'Wandern')
        self.assertEqual(self.db.execute("SELECT category FROM books WHERE id=681").fetchone()[0],
                         'Weitwanderführer')

    def test_cleanup_is_one_time_and_preserves_later_manual_edits(self):
        self.assertGreater(migrate(self.db),0)
        self.db.execute("UPDATE books SET category='Eigene Kategorie' WHERE id=4")
        self.db.commit()
        self.assertEqual(migrate(self.db),0)
        self.assertEqual(self.db.execute("SELECT category FROM books WHERE id=4").fetchone()[0],
                         'Eigene Kategorie')
        marker=self.db.execute("SELECT changed_rows FROM data_cleanup_migrations WHERE version=?",
                               (VERSION,)).fetchone()
        self.assertIsNotNone(marker)

    def test_second_cleanup_consolidates_categories_and_metadata(self):
        self.db.execute("""INSERT INTO books
            (id,title,author,isbn,category,publisher,published,area,topic,book_index,description)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (77,'Tennegebirge Hochkönig Nr. 15 1:50000','Unbekannt','9783990447215',
             'AV-Karte','Rother','2019','Gottard','Wandern, Bergsteigen','KNK 17',
             json.dumps({'Auflagedatum':'2019'},ensure_ascii=False)))
        self.db.execute("""INSERT INTO books
            (id,title,author,isbn,category,publisher,published,area,topic,book_index,description)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (554,'Gottardweg Basel - Mailand','Unbekannt','9783763340002',
             'Wanderführer alpin','SAC','2010','Gottard','Kompass Wanderbuch','FN 1',
             json.dumps({'Auflagedatum':'2010'},ensure_ascii=False)))
        self.db.commit()
        changed=migrate_v2(self.db)
        self.assertEqual(changed,2)
        r=self.db.execute("SELECT * FROM books WHERE id=77").fetchone()
        self.assertEqual(r['category'],'Alpenvereinskarte')
        self.assertEqual(r['publisher'],'Bergverlag Rother')
        self.assertEqual(r['area'],'Gotthard')
        self.assertEqual(r['topic'],'Bergsteigen Wandern')
        self.assertIn('Tennengebirge',r['title'])
        r=self.db.execute("SELECT * FROM books WHERE id=554").fetchone()
        self.assertEqual(r['category'],'Wanderführer')
        self.assertEqual(r['publisher'],'SAC Verlag')
        self.assertEqual(r['topic'],'Wandern')
        self.assertIn('Gotthardweg',r['title'])
        self.assertEqual(migrate_v2(self.db),0)
        self.assertIsNotNone(self.db.execute(
            "SELECT 1 FROM data_cleanup_migrations WHERE version=?",(VERSION_V2,)).fetchone())



if __name__=='__main__':
    unittest.main()
