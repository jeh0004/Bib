#!/usr/bin/env python3
"""Read-only verification of original and migrated SQLite copies."""
import sqlite3
import sys
from pathlib import Path

TABLES = ("users", "books", "loans", "settings")
NEW_TABLES = ("book_copies", "loan_policy", "tags", "genres", "members")


def inspect(path):
    if not Path(path).is_file():
        raise ValueError(f"Datei fehlt: {path}")
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Integritaetsfehler in {path}: {integrity}")
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(TABLES) - tables
        if missing:
            raise ValueError(f"Fehlende Tabellen in {path}: {sorted(missing)}")
        counts = {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
        print(f"{path}: integrity_check=ok, Datenbestand={counts}")
        return counts, tables
    finally:
        con.close()


def main():
    if len(sys.argv) not in (2, 3):
        print("Aufruf: check_test_copy.py ORIGINAL.db [MIGRIERT.db]", file=sys.stderr)
        return 2
    original, _ = inspect(sys.argv[1])
    if len(sys.argv) == 3:
        migrated, tables = inspect(sys.argv[2])
        if original != migrated:
            raise ValueError(f"Bestandszahlen veraendert: vorher={original}, nachher={migrated}")
        missing = set(NEW_TABLES) - tables
        if missing:
            raise ValueError(f"Phase-3-Tabellen fehlen: {sorted(missing)}")
        print("PASS: Grundbestand unveraendert, Phase-3-Tabellen vorhanden")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, sqlite3.Error) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
