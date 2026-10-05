# LeihGut – Entwicklungsstand (nicht produktiv einsetzen)

Dieser Branch stellt die ursprüngliche Flask-Projektstruktur mit `templates/`, `static/` und `static/fonts/` wieder her. Die bislang hochgeladenen Phase-3-Dateien wurden in `migrations/` und `tests/` einsortiert.

**Wichtig:** Die hochgeladene Phase-3-ZIP ist kein vollständiger Repository-Ersatz: Es fehlen insbesondere die in der Anleitung genannten Pakete `catalog_extra/` und `loans_extra/` sowie ihre HTML-Vorlagen. Daher sind die Migrationen absichtlich noch nicht in `app.py` eingebunden. Die ursprüngliche Anwendung bleibt so zunächst lauffähig. Die weiteren Funktionen und die End-to-End-Tests stehen noch aus.

Die alten Entwicklungsnotizen stehen in `PHASE3_NOTES.md`. Keine Produktionsdatenbank mit diesem Entwicklungsstand migrieren.
