# LeihGut – Library Management for Clubs

A lightweight, self-hosted library management system designed for small clubs and associations. Members can browse the catalogue, reserve books, and log returns — all through a clean web interface.

![Python](https://img.shields.io/badge/Python-3.12-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-lightgrey)
![SQLite](https://img.shields.io/badge/Database-SQLite-green)
![Docker](https://img.shields.io/badge/Docker-ready-blue)

---

## Features

- **Book catalogue** — searchable by title, author or category; shows real-time availability
- **Reservations** — members reserve online, pick up at the club
- **Returns** — members log their own returns; trust-based workflow
- **Two roles** — Admins manage books and users; Members borrow and return
- **Customisable** — set your club name, brand colour and logo via the admin panel
- **Secure by default** — CSRF protection, brute-force lockout, password policy, security headers, non-root container
- **No external dependencies** — single SQLite database, no mail server required

---

## Quick Start

Create a `docker-compose.yml`:

```yaml
services:
  web:
    image: ninelivesdev/leihgut:latest
    ports:
      - "8080:5000"
    volumes:
      - db_data:/data
    environment:
      - COOKIE_SECURE=false   # set to "true" if running behind HTTPS
    restart: unless-stopped

volumes:
  db_data:
```

Then start the container:

```bash
docker compose up -d
```

Open **http://localhost:8080** in your browser.

**Default credentials:**
| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `admin` |

> You will be prompted to set a new password on first login.

---

## Configuration

All configuration is done via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `COOKIE_SECURE` | `false` | Set to `true` when running behind HTTPS |
| `SECRET_KEY` | *(auto-generated)* | Flask session key — leave empty for auto-generation |
| `DATABASE` | `/data/vereinsbibliothek.db` | Path to the SQLite database |
| `UPLOAD_DIR` | `/data/uploads` | Directory for logo uploads |

The secret key is automatically generated on first start and persisted to `/data/.secret_key`. The `/data` volume preserves all data across container restarts and updates.

---

## Customisation

After logging in, go to **Admin → Appearance** to configure:

- **Club name** — displayed in the navbar and page title
- **Brand colour** — applied to buttons, links and badges
- **Club logo** — PNG, JPG or SVG, shown in the navbar and login screen

---

## Updating

```bash
docker compose pull
docker compose up -d
```

Your data is stored in the `db_data` volume and is not affected by updates.

---

## Backup

```bash
# Create a backup
docker run --rm \
  -v db_data:/data \
  -v $(pwd):/backup \
  alpine cp /data/vereinsbibliothek.db /backup/leihgut_backup_$(date +%Y%m%d).db
```

---

## Security Notes

- After **5 failed login attempts**, the account is locked for 15 minutes
- All passwords must be at least 12 characters with upper/lowercase, digits and special characters
- New users receive a generated initial password and must change it on first login
- CSRF protection is enabled on all forms
- The container runs as a non-root user
- Set `COOKIE_SECURE=true` and run behind a reverse proxy (nginx, Caddy) for production use

---

## Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.12 / Flask 3 |
| Database | SQLite |
| Frontend | Bootstrap 5.3 |
| Server | Gunicorn |

---

## Source

[github.com/ninelivesdev123/leihgut](https://github.com/ninelivesdev123/leihgut)
