"""One-time, idempotent cleanup for the 681-title legacy catalog."""
import json
import re

VERSION = "2026-10-07-catalog-cleanup-v1"

CATEGORY_FIXES = {
    "Alpin Lehrplan": "Alpin-Lehrplan",
    "Burgen-Führer": "Burgenführer",
    "Jubiläums Wanderführer": "Jubiläums-Wanderführer",
    "SAC Führer": "SAC-Führer",
    "Schneeschuh Führer": "Schneeschuhführer",
    "Schneeschuhuhführer anderführer": "Schneeschuhführer",
    "Skitorenführer": "Skitourenführer",
    "Skitournführer": "Skitourenführer",
    "Wamderführer": "Wanderführer",
    "Wanderfürer": "Wanderführer",
    "Weitwnderführer": "Weitwanderführer",
}

TOPIC_FIXES = {
    "Bergsteigen, WAndern": "Bergsteigen, Wandern",
    "Waqndern": "Wandern",
    "Bergsteigen andern Rad Ski": "Bergsteigen Wandern Rad Ski",
    "Höhllen Eiszeitkunst": "Höhlen Eiszeitkunst",
    "Wanern": "Wandern",
}

AREA_FIXES = {
    "Allgäu Auße4rfern": "Allgäu Außerfern",
    "Alöbtadt": "Albstadt",
    "Ammergauer Alp.": "Ammergauer Alpen",
    "Bayer.Alpen": "Bayerische Alpen",
    "Bayerische Alpe": "Bayerische Alpen",
    "Chiemgauer Alpe": "Chiemgauer Alpen",
    "Chur Hinterrhei": "Chur Hinterrhein",
    "Dachstein Tauer": "Dachstein Tauern",
    "Glarus Appenzel": "Glarus Appenzell",
    "Glarus St.Galle": "Glarus St. Gallen",
    "Gottard": "Gotthard",
    "Großes Walserta": "Großes Walsertal",
    "Ibiza Formenter": "Ibiza Formentera",
    "Kitzbüheler Alp": "Kitzbüheler Alpen",
    "Ligurische Berg": "Ligurische Berge",
    "Malta Gozo Camino": "Malta Gozo Comino",
    "Slalzburger Land": "Salzburger Land",
    "Säntis Churfirs": "Säntis Churfirsten",
    "Tessiner Voralp": "Tessiner Voralpen",
    "USA Sierra Neva": "USA Sierra Nevada",
    "Ötztal Silvrett": "Ötztal Silvretta",
    "Sextener Dolomi": "Sextener Dolomiten",
    "berstaufen": "Oberstaufen",
    "Starnbergersee": "Starnberger See",
}

TITLE_FIXES = {
    30: ("Westlatt", "Westblatt"),
    133: ("vertkal", "vertikal"),
    134: ("vertkal", "vertikal"),
    201: ("Finsreraarhorn", "Finsteraarhorn"),
    298: ("Byerische", "Bayerische"),
}

PUBLISHER_FIXES = {
    31: "Alpenverein",
    42: "W. Hartung",
    107: "Sidarta",
    237: "J. Berg",
    348: "Rother",
}

ISBN_FIXES = {
    26: "3-7633-1123-8",
    27: "3-928777-14-9",
    35: "3-928777-31-5",
    61: "3-85431-271-7",
    66: "3-7633-3010-0",
    79: "978-3-85491-109-8",
    81: "978-3-86038-436-7",
    82: "978-3-89933-116-5",
    202: "978-3-85256-322-0",
    203: "978-3-85256-278-0",
    219: "978-3-7654-5149-2",
    247: "978-3-95611-062-7",
    631: "978-3-85869-432-4",
    644: "978-3-85026-017-6",
    657: "978-3-7633-4022-4",
    664: "3-7633-4319-9",
    665: "978-3-7633-4341-6",
    669: "978-3-7654-2433-5",
}

ISBN_PLACEHOLDERS = {"keine", "keine 6", "keine 7", "fehlt?", "vorhabden"}
TEXT_COLUMNS = ("title", "author", "category", "publisher", "published", "area", "topic", "book_index")


def _clean_space(value):
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value).replace("\u00a0", " ")).strip()


def migrate(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "books" not in tables:
        return 0

    db.execute("""CREATE TABLE IF NOT EXISTS data_cleanup_migrations (
        version TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now')),
        changed_rows INTEGER NOT NULL DEFAULT 0
    )""")
    if db.execute("SELECT 1 FROM data_cleanup_migrations WHERE version=?", (VERSION,)).fetchone():
        return 0

    columns = {r[1] for r in db.execute("PRAGMA table_info(books)")}
    selectable = ["id"] + [c for c in (
        "title","author","isbn","category","publisher","published",
        "area","topic","book_index","description"
    ) if c in columns]
    if "id" not in columns or "title" not in columns:
        return 0

    rows = db.execute("SELECT " + ",".join(selectable) + " FROM books ORDER BY id").fetchall()
    changed_rows = 0

    with db:
        for raw in rows:
            row = dict(zip(selectable, raw)) if not hasattr(raw, "keys") else {k: raw[k] for k in selectable}
            book_id = row["id"]
            updates = {}

            for column in TEXT_COLUMNS:
                if column in row:
                    cleaned = _clean_space(row[column])
                    if cleaned != row[column]:
                        updates[column] = cleaned

            category = updates.get("category", row.get("category"))
            if category in CATEGORY_FIXES:
                updates["category"] = CATEGORY_FIXES[category]

            topic = updates.get("topic", row.get("topic"))
            if topic in TOPIC_FIXES:
                updates["topic"] = TOPIC_FIXES[topic]

            area = updates.get("area", row.get("area"))
            if area in AREA_FIXES:
                updates["area"] = AREA_FIXES[area]

            title = updates.get("title", row.get("title", ""))
            if book_id in TITLE_FIXES:
                old, new = TITLE_FIXES[book_id]
                if old in title:
                    updates["title"] = title.replace(old, new)

            if "publisher" in row and book_id in PUBLISHER_FIXES:
                updates["publisher"] = PUBLISHER_FIXES[book_id]

            if "isbn" in row:
                isbn = _clean_space(row["isbn"])
                if isbn and isbn.lower() in ISBN_PLACEHOLDERS:
                    updates["isbn"] = None
                elif book_id in ISBN_FIXES:
                    updates["isbn"] = ISBN_FIXES[book_id]
                elif isbn != row["isbn"]:
                    updates["isbn"] = isbn

            if "description" in row and row["description"]:
                description = row["description"]
                parsed = None
                try:
                    parsed = json.loads(description)
                except (ValueError, TypeError):
                    repaired = description.replace("\\.", ".")
                    try:
                        parsed = json.loads(repaired)
                        updates["description"] = repaired
                    except (ValueError, TypeError):
                        pass
                if isinstance(parsed, dict) and "published" in row:
                    edition = _clean_space(parsed.get("Auflagedatum"))
                    match = re.fullmatch(r"(\d{4})(?:\s*\?)?", edition or "")
                    if match:
                        updates["published"] = match.group(1)

            updates = {k: v for k, v in updates.items() if k in row and v != row[k]}
            if updates:
                db.execute(
                    "UPDATE books SET " + ",".join(f"{k}=?" for k in updates) + " WHERE id=?",
                    list(updates.values()) + [book_id],
                )
                changed_rows += 1

        db.execute(
            "INSERT INTO data_cleanup_migrations(version,changed_rows) VALUES(?,?)",
            (VERSION, changed_rows),
        )
    return changed_rows
