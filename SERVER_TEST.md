# Phase-3-Test auf einem bestehenden Docker-Compose-Server

**Kein Produktiv-Update.** Alle Schritte erfolgen per SSH auf dem Server, aber in einem **separaten Testverzeichnis**. Erst die bestehende Compose-Datei ohne Zugangsdaten prüfen lassen, bevor produktive Containernamen/Volumes eingesetzt werden.

## A. Bestehende Konfiguration ermitteln (nur lesend)

```sh
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
docker inspect CONTAINERNAME --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
docker inspect CONTAINERNAME --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}'
```

Falls der Container nicht über Compose gestartet wurde, kann das Label fehlen. Im gefundenen Ordner mit `ls -la` nach `compose.yml`, `compose.yaml`, `docker-compose.yml` oder `docker-compose.yaml` suchen. **`docker compose config` nicht ungefiltert teilen**, weil es Umgebungsvariablen/Secrets auflösen kann.

Auf dem eigenen Computer (nicht innerhalb der SSH-Sitzung):
```sh
scp BENUTZER@SERVER:/ABSOLUTER/PFAD/docker-compose.yml ./docker-compose.yml
```
Bei SSH-Port ungleich 22: `scp -P PORT ...`. Windows PowerShell, macOS und Linux unterstützen in der Regel `scp`. Zugangsdaten vor dem Teilen entfernen.

## B. Vorbereitung (nach Prüfung der bestehenden Compose-Datei)

```sh
mkdir -p ~/leihgut-phase3-test
cd ~/leihgut-phase3-test
git clone --branch entwicklung-phase3 https://github.com/jeh0004/Bib.git .
docker compose -p leihgut-phase3-sandbox -f compose.test.yml config
```
Port `127.0.0.1:8081` ist absichtlich nur auf dem Server erreichbar. Falls er bereits belegt ist, Port in `compose.test.yml` ändern. **Nie** das Produktiv-Volume als Test-Volume eintragen.

## C. Konsistente Kopie der produktiven SQLite-Datenbank

Die Datenbankkopie **muss** über die SQLite-Backup-API aus dem laufenden Container erstellt werden; ein direktes Kopieren der .db-Datei bei Schreibzugriff ist nicht ausreichend. Zuerst den tatsächlichen Datenbankpfad und Containernamen aus der geprüften Konfiguration ermitteln. Nachfolgend sind `PRODUKTIVCONTAINER` und `/data/vereinsbibliothek.db` Beispiele:

```sh
docker exec PRODUKTIVCONTAINER python -c 'import sqlite3; src=sqlite3.connect("file:/data/vereinsbibliothek.db?mode=ro",uri=True); dst=sqlite3.connect("/tmp/leihgut-phase3-backup.db"); src.backup(dst); dst.close(); src.close()'
docker cp PRODUKTIVCONTAINER:/tmp/leihgut-phase3-backup.db ./leihgut-phase3-backup.db
docker exec PRODUKTIVCONTAINER rm -f /tmp/leihgut-phase3-backup.db
python3 scripts/check_test_copy.py ./leihgut-phase3-backup.db
```

Bei anderem Datenbankpfad **vorher** den Befehl anpassen. Backup-Datei vertraulich behandeln; sie enthält Mitgliederdaten. Wenn die App Uploads verwendet, diese **ebenfalls als Kopie** in den Testcontainer übertragen. Uploads nicht mit dem Produktivvolume verbinden.

## D. Testdatenbank in das eigene Test-Volume laden

```sh
docker compose -p leihgut-phase3-sandbox -f compose.test.yml run --rm --no-deps -T --entrypoint sh web -c 'cat > /data/vereinsbibliothek.db' < ./leihgut-phase3-backup.db
docker compose -p leihgut-phase3-sandbox -f compose.test.yml up -d --build
docker compose -p leihgut-phase3-sandbox -f compose.test.yml ps
docker compose -p leihgut-phase3-sandbox -f compose.test.yml logs --tail=100
```

Für die Integritätsprüfung eine **Kopie der migrierten Test-DB** herausziehen:
```sh
TEST_ID=$(docker compose -p leihgut-phase3-sandbox -f compose.test.yml ps -q web)
docker cp "$TEST_ID":/data/vereinsbibliothek.db ./leihgut-phase3-migrated.db
python3 scripts/check_test_copy.py ./leihgut-phase3-backup.db ./leihgut-phase3-migrated.db
```
Diese Prüfung vergleicht zentrale Datensatzanzahlen, überprüft `PRAGMA integrity_check` und die Phase-3-Tabellen. Die Anzahl allein beweist nicht die vollständige Funktionsfähigkeit.

## E. Browserprüfung ohne öffentlichen Testport

Vom eigenen Computer aus einen SSH-Tunnel öffnen:
```sh
ssh -L 8081:127.0.0.1:8081 BENUTZER@SERVER
```
Dann `http://localhost:8081` öffnen. Login, Rollen, alte Reservierungen, Rückgaben, neue Exemplare, Verlängerungen, ISBN und Neustart prüfen. Testdaten bleiben im separaten Volume.

## F. Aufräumen

```sh
docker compose -p leihgut-phase3-sandbox -f compose.test.yml down
```
**Kein `down -v`**, solange Testdaten noch zur Fehleranalyse benötigt werden. Für vollständiges Löschen der Testdaten erst nach Freigabe `down -v` verwenden. Das Produktionsprojekt niemals stoppen oder ändern.

**Nicht als Produktionsfreigabe interpretieren:** Legacy-Routen, echte Sessions/CSRF und Migration echter Bestandsdaten erfordern weitere Integrationstests.
