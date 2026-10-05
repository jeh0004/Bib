# LeihGut – Installationsanleitung

Bibliotheksverwaltung für Vereine. Mitglieder können Bücher im Katalog einsehen, reservieren und Rückgaben vermerken. Administratoren verwalten Bücher und Nutzer.

---

## Voraussetzungen

- [Docker](https://docs.docker.com/get-docker/) (Desktop oder Engine)
- [Docker Compose](https://docs.docker.com/compose/) (ab v2, bei Docker Desktop bereits enthalten)

```bash
docker --version        # z. B. Docker version 25.0.0
docker compose version  # z. B. Docker Compose version v2.24.0
```

---

## Installation & Start

```bash
# 1. In den Projektordner wechseln
cd vereinsbibliothek

# 2. Container bauen und starten
docker compose up --build -d

# 3. Initialpasswort des Admin-Accounts ablesen
docker logs vereinsbibliothek-web-1 2>&1 | grep -A3 "INITIALPASSWORT"
```

Die Anwendung ist anschließend erreichbar unter:
**http://localhost:8080**

---

## Erster Login

1. Benutzername: `admin`
2. Passwort: aus dem Log (siehe oben)
3. Nach dem Login wirst du automatisch zur Passwort-Änderung weitergeleitet
4. Neues Passwort muss erfüllen:
   - Mindestens 12 Zeichen
   - Groß- und Kleinbuchstaben
   - Mindestens eine Ziffer
   - Mindestens ein Sonderzeichen (`!@#$%` …)

---

## Admin-Passwort zurücksetzen

Falls das Admin-Passwort vergessen wurde:

```bash
# Container stoppen
docker compose down

# Datenbank direkt bearbeiten
docker run --rm -v vereinsbibliothek_db_data:/data alpine \
  sh -c "apk add --no-cache sqlite && sqlite3 /data/vereinsbibliothek.db \
  \"UPDATE users SET must_change_password=1, \
  password_hash='\$(python3 -c \\\"from werkzeug.security import generate_password_hash; print(generate_password_hash(\\\\\\\"Admin#Temp2024!\\\\\\\"))\\\")' \
  WHERE username='admin';\""

# Oder einfacher: Volume löschen und neu starten (alle Daten gehen verloren!)
docker compose down -v
docker compose up -d
# → Neues Initialpasswort erscheint im Log
```

**Empfohlen:** Einen zweiten Admin-Account anlegen, damit gegenseitiges Zurücksetzen möglich ist.

---

## Verein anpassen

Nach dem Login unter **Admin → Darstellung**:

| Einstellung | Beschreibung |
|---|---|
| Vereinsname | Wird in Navbar und Seitentitel angezeigt |
| Vereinsfarbe | Primärfarbe für Buttons, Links, Badges |
| Vereinslogo | PNG/JPG/SVG, max. 2 MB, wird in Navbar und Login-Seite angezeigt |

---

## Nutzerverwaltung

| Rolle | Berechtigungen |
|---|---|
| **Admin** | Bücher & Nutzer anlegen/bearbeiten, Ausleihen bestätigen |
| **Mitglied** | Katalog einsehen, Bücher reservieren & zurückgeben |

**Neues Mitglied anlegen:** Admin → Benutzer → „Mitglied einladen"  
→ Ein generiertes Initialpasswort wird angezeigt — bitte persönlich weitergeben.  
→ Das Mitglied muss es beim ersten Login ändern.

**Passwort zurücksetzen:** Admin → Benutzer → ↺-Button  
→ Neues Initialpasswort wird angezeigt.

---

## Betrieb & Wartung

### Container-Status prüfen
```bash
docker compose ps
docker logs vereinsbibliothek-web-1 --tail 50
```

### Container neu starten
```bash
docker compose restart
```

### Anwendung aktualisieren (nach Code-Änderungen)
```bash
docker compose down
docker compose up --build -d
```

### Daten-Backup (SQLite-Datenbank)
```bash
# Backup erstellen
docker run --rm \
  -v vereinsbibliothek_db_data:/data \
  -v $(pwd):/backup \
  alpine cp /data/vereinsbibliothek.db /backup/backup_$(date +%Y%m%d).db

# Backup wiederherstellen
docker run --rm \
  -v vereinsbibliothek_db_data:/data \
  -v $(pwd):/backup \
  alpine cp /backup/backup_20240101.db /data/vereinsbibliothek.db
```

### Datenbank direkt abfragen (Diagnose)
```bash
docker run --rm -it \
  -v vereinsbibliothek_db_data:/data \
  alpine sh -c "apk add --no-cache sqlite && sqlite3 /data/vereinsbibliothek.db"
```

---

## Sicherheitshinweise

- Den Container **nicht direkt** ohne Reverse-Proxy (nginx, Caddy) ins Internet stellen
- Für HTTPS-Betrieb: `COOKIE_SECURE=true` in `docker-compose.yml` setzen
- Der Secret Key wird automatisch generiert und in `/data/.secret_key` gespeichert — diese Datei nicht löschen (invalidiert alle Sessions)
- Nach 5 fehlgeschlagenen Loginversuchen wird ein Account für 15 Minuten gesperrt

---

## Konfiguration (docker-compose.yml)

| Variable | Standard | Beschreibung |
|---|---|---|
| `DATABASE` | `/data/vereinsbibliothek.db` | Pfad zur SQLite-Datenbank |
| `COOKIE_SECURE` | `false` | Auf `true` setzen wenn HTTPS aktiv |
| `SECRET_KEY` | *(auto-generiert)* | Flask Session-Key — leer lassen für Auto-Generierung |
| `UPLOAD_DIR` | `/data/uploads` | Verzeichnis für Logo-Uploads |

---

## Projektstruktur

```
vereinsbibliothek/
├── app.py              # Gesamte Anwendungslogik (Flask)
├── schema.sql          # Datenbankschema
├── requirements.txt    # Python-Abhängigkeiten
├── Dockerfile          # Container-Definition
├── docker-compose.yml  # Start-Konfiguration
├── static/
│   └── style.css       # Zusätzliche Styles
└── templates/          # HTML-Templates (Jinja2)
    ├── base.html
    ├── login.html
    ├── catalog.html
    ├── ...
    └── admin/
```
