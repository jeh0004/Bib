# AMD64-Image: Veröffentlichung und Serverinstallation

**Entwicklungsimage:** `ghcr.io/jeh0004/bib:phase3-amd64-test`.
Der GitHub-Actions-Workflow `Build AMD64 test image` führt Python-Tests, einen echten AMD64-Build, die Architekturprüfung und einen HTTP-Starttest aus. **Nur danach** veröffentlicht er das Image in GitHub Container Registry (GHCR). Die erfolgreiche Ausführung muss vor dem Einsatz kontrolliert werden. Dies ist **keine Freigabe aller Phase-3-Funktionen**.

## GHCR-Paket freigeben

Falls das Paket zunächst privat ist, auf GitHub unter Profil → Packages → `bib` → Package settings → Change visibility auf **Public** umstellen. Andernfalls benötigt der Server ein gültiges GHCR-Token mit `read:packages`; keine Tokens in Compose-Dateien speichern.

## Auf dem Server (/root/leihgut)

Deine aktuelle Konfiguration nutzt `leihgut`, Port `127.0.0.1:5000:5000` und das Compose-Volume `db_data`. **Keine Volumes löschen.** Erst nach erfolgreicher Veröffentlichung und Freigabe der gewünschten Version die Konfiguration sichern:

```sh
cd /root/leihgut
cp docker-compose.yml docker-compose.yml.bak
```

In der vorhandenen `docker-compose.yml` genau diese zwei Zeilen ändern:

```yaml
    image: ghcr.io/jeh0004/bib:phase3-amd64-test
    platform: linux/amd64
```

Rest unverändert lassen. Dann:
```sh
docker compose pull
docker compose up -d --no-deps
docker compose ps
docker compose logs --tail=60
```

Der Compose-Projektname, der Dienstname `leihgut`, die Portbindung und `db_data:/data` bleiben erhalten. `docker compose down -v` **niemals** ausführen. Für den produktiven Einsatz zuerst die noch offenen Integrations- und Importfunktionen fertigstellen und abnehmen. Ein erfolgreicher Build allein reicht nicht.
