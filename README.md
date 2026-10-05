# LeihGut – Phase 3 Entwicklungsbranch

Die ursprüngliche Verzeichnisstruktur ist wiederhergestellt. Die Pakete `catalog_extra/` und `loans_extra/` sind eingebunden. Die Datenbankmigrationen `migrations/catalog.py` und `migrations/loans.py` werden beim Start ausgeführt.

**Nur Testumgebung:** Die alten Ausleih- und Reservierungsrouten bleiben aktiv und sind noch nicht vollständig auf konkrete Exemplare umgestellt. Benachrichtigungen, Mahnwesen, Rollen, Statistik, Export und API-Erweiterungen sind noch nicht umgesetzt. Keine produktive Installation oder Datenbankmigration ohne Backup und vollständige Tests.

Neue Admin-Pfade: `/admin/catalog-extra/` und `/admin/loans-extra/`. Ursprüngliche Notizen: `PHASE3_NOTES.md`.
