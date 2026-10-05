# LeihGut – Phase 3: Exemplar-Ausleihe

Dieses **Erweiterungspaket** enthält Phase 1/2 und neue, separate Admin-Seiten für exemplarbasierte Ausleihe, Rückgabe, Verlängerung und Leihfristen. Es ist **kein vollständiger Repository-Ersatz**.

## Installation

1. **SQLite-Datenbank und Docker-Volume sichern.** Vorzugsweise zunächst in einer Testumgebung einsetzen.
2. `migrations/`, `catalog_extra/`, `loans_extra/` ins Stammverzeichnis des LeihGut-Repositories kopieren (vorhandene Dateien aus Phase 1/2 entsprechend ersetzen).
3. In `app.py` innerhalb `init_db()` direkt nach `db.executescript(f.read().decode())` ergänzen:

```python
from migrations.catalog import migrate as migrate_catalog
from migrations.loans import migrate as migrate_loans
migrate_catalog(db)
migrate_loans(db)
```

4. In `app.py` direkt nach `app = Flask(__name__)` ergänzen:

```python
from catalog_extra import bp as catalog_extra_bp
from loans_extra import bp as loans_extra_bp
app.register_blueprint(catalog_extra_bp)
app.register_blueprint(loans_extra_bp)
```

5. Falls Phase 2 bereits installiert ist, die **alten Import-/Registrierungszeilen nicht doppelt hinzufügen**, sondern anpassen.
6. `docker compose up -d --build` und als Admin `/admin/loans-extra/` öffnen.

## Einschränkungen

- Die alten Ausleih-/Reservierungsrouten bleiben unverändert aktiv. Die neue Ausleihe berücksichtigt zwar deren Kapazitätsverbrauch, aber alte Buch-Ausleihen erhalten **keine** erfundene Exemplarzuordnung. Solange die alten Routen aktiv sind, ist keine lückenlose Exemplarzuordnung über alle Vorgänge garantiert.
- Die neue Oberfläche ermöglicht Ausleihe an aktive Benutzer, Rückgabe und maximal konfigurierbare Verlängerungen; sie verhindert Verlängerungen bei Reservierungen und prüft freie Kapazität.
- Bestehende ausgeliehene Bücher ohne Fälligkeitsdatum werden nicht rückwirkend verändert.
- Reservierungsfreigabe, Benachrichtigungen, Mahnungen, Mitgliedsverwaltung und Umstellung der bestehenden Buchrouten stehen noch aus.
- Für vollständigen Produktivbetrieb ist die Integration in die bestehenden Routen und ein Ende-zu-Ende-Test erforderlich.
