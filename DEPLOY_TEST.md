# Test und Rollout über Docker Compose (SSH)

**Status: Entwicklungsstand. Nicht auf dem Produktivserver ausführen.**

## Tests lokal / CI
```sh
python -m unittest discover -s tests -v
python -m compileall -q app.py catalog_extra loans_extra migrations
docker compose config
docker compose build
```
Mit Testdaten: Anmeldung, Admin-Menü, ISBN (auch Open-Library-Ausfall), Reservierung, Ausleihe, Rückgabe, Verlängerung, Buch-/Exemplar-Änderung und Wiederanlauf prüfen.

## Datenbank-Upgrade testen
1. Eine **Kopie** der bestehenden SQLite-Datei und Uploads in eine getrennte Testumgebung kopieren; Original unangetastet lassen.
2. In der Testumgebung Docker Compose starten und Migration prüfen.
3. Bestandszahlen, aktive Ausleihen, Rollen, Zugriffe und Daten nach Neustart vergleichen.
4. Backup-Restore auf einer weiteren Testkopie durchführen.
5. Bei Fehlern **nicht** auf dem Produktivserver deployen.

## Produktiver Rollout – erst nach Freigabe
```sh
ssh <server>
cd <bestehender-leihgut-ordner>
docker compose ps
# Backup des kompletten /data-Volumes, der Compose-Datei und Umgebungsvariablen
# vorher erstellen und Wiederherstellung prüfen.
git fetch origin
git checkout <geprüfter-release-tag>
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=100
```
Wichtig: Das Repository muss der tatsächlich verwendete Checkout sein; keine blind übernommenen Pfade oder Befehle. Die Migration verändert SQLite. Ein Git-Rollback allein stellt die Datenbank nicht wieder her.

## Noch vor Freigabe offen
- Vollständige Ende-zu-Ende-Tests inklusive echter Flask-Sitzungen und CSRF
- Einheitliche Exemplarzuordnung der bisherigen Ausleih- und Reservierungsrouten
- Fehlerfälle bei Bestandsreduktion und alten Ausleihen
- Docker-Build und Upgrade-/Restore-Test auf echter Testumgebung
- Release-Tag und Merge nach main **erst** nach bestandener Prüfung
