"""Second one-time cleanup: consolidate category taxonomy and further high-confidence inconsistencies."""
import re

VERSION = "2026-10-07-catalog-cleanup-v2"

CATEGORY_FIXES = {
    "AV-Führer": "Alpenvereinsführer",
    "AV-Karte": "Alpenvereinskarte",
    "MTB-Führer": "Mountainbikeführer",
    "Skilanglaufführer": "Langlaufführer",
    "SAC Auswahlführer": "SAC-Auswahlführer",
    "Wanderführer alpin": "Wanderführer",
    "Wanderführer+2x Karten": "Wanderführer",
    "Karte Radführer": "Radführer",
    "Lehrplan": "Alpin-Lehrplan",
}

TOPIC_FIXES = {
    "Bergsteigen, Wandern": "Bergsteigen Wandern",
    "Wandern, Bergsteigen": "Bergsteigen Wandern",
    "Bergsteigen, Wandern, Rad": "Bergsteigen Wandern Rad",
    "Bergsteigen, Wandern Ski": "Bergsteigen Wandern Ski",
    "Mountainbiken": "Mountainbike",
    "Skilanglauf": "Langlauf",
    "Hochtourenführer": "Hochtouren",
    "Wanderführer": "Wandern",
    "Kompass Wanderbuch": "Wandern",
    "Schneeschuhgehen": "Schneeschuhwandern",
    "Schneeschuh": "Schneeschuhwandern",
}

PUBLISHER_FIXES = {
    "Bergverlag Rudolf Rother,Germany": "Bergverlag Rother",
    "Rother Verlag": "Bergverlag Rother",
    "Rother": "Bergverlag Rother",
    "Rother Selectio": "Rother Selection",
    "Rother Selektion": "Rother Selection",
    "SAC": "SAC Verlag",
    "SAC-Verlag": "SAC Verlag",
    "Panico": "Panico Alpinverlag",
    "Panico Alpin": "Panico Alpinverlag",
    "Panico Alpin Verlag": "Panico Alpinverlag",
    "BLV Buchverlag": "BLV",
    "blv": "BLV",
    "Weitwanderverla": "Weitwanderverlag",
    "Tyrolia Verlagsanstalt Gm": "Tyrolia Verlagsanstalt GmbH",
}

TITLE_REPLACEMENTS = {
    77: ("Tennegebirge", "Tennengebirge"),
    158: ("Außergfern", "Außerfern"),
    421: ("Tartra", "Tatra"),
    465: ("Fernwanderwg E1 Deutschlan Süd", "Fernwanderweg E1 Deutschland Süd"),
    554: ("Gottardweg", "Gotthardweg"),
}

EXACT_BOOK_FIXES = {
    464: {
        "isbn": "9783771805050",
        "author": "Hans Schmidt",
    },
}

AREA_FIXES = {
    "Costa del Azahr": "Costa del Azahar",
    "Gottard": "Gotthard",
}


def migrate(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "books" not in tables:
        return 0
    db.execute("""CREATE TABLE IF NOT EXISTS data_cleanup_migrations (
        version TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now')),
        changed_rows INTEGER NOT NULL DEFAULT 0
    )""")
    if db.execute("SELECT 1 FROM data_cleanup_migrations WHERE version=?",(VERSION,)).fetchone():
        return 0

    columns={r[1] for r in db.execute("PRAGMA table_info(books)")}
    needed={"id","title"}
    if not needed.issubset(columns):
        return 0
    selected=["id"]+[c for c in ("title","author","isbn","category","topic","publisher","area") if c in columns]
    rows=db.execute("SELECT "+",".join(selected)+" FROM books ORDER BY id").fetchall()
    changed=0
    with db:
        for raw in rows:
            row=dict(zip(selected,raw)) if not hasattr(raw,"keys") else {k:raw[k] for k in selected}
            updates={}
            if "category" in row and row["category"] in CATEGORY_FIXES:
                updates["category"]=CATEGORY_FIXES[row["category"]]
            if "topic" in row and row["topic"] in TOPIC_FIXES:
                updates["topic"]=TOPIC_FIXES[row["topic"]]
            if "publisher" in row and row["publisher"] in PUBLISHER_FIXES:
                updates["publisher"]=PUBLISHER_FIXES[row["publisher"]]
            if "area" in row and row["area"] in AREA_FIXES:
                updates["area"]=AREA_FIXES[row["area"]]
            if row["id"] in TITLE_REPLACEMENTS:
                old,new=TITLE_REPLACEMENTS[row["id"]]
                if old in row["title"]:
                    updates["title"]=row["title"].replace(old,new)
            for field,value in EXACT_BOOK_FIXES.get(row["id"],{}).items():
                if field in row:
                    updates[field]=value
            updates={k:v for k,v in updates.items() if v!=row.get(k)}
            if updates:
                db.execute("UPDATE books SET "+",".join(f"{k}=?" for k in updates)+" WHERE id=?",
                           list(updates.values())+[row["id"]])
                changed+=1
        db.execute("INSERT INTO data_cleanup_migrations(version,changed_rows) VALUES(?,?)",(VERSION,changed))
    return changed
