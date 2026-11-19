# desktop : édition locale Windows de Komersia

Lanceur (PostgreSQL embarqué + serveur web waitress + planificateur) et chaîne de construction de l'installateur
`Komersia-Setup-<version>.exe`. Documentation complète : [`docs/dev/edition-locale.md`](../docs/dev/edition-locale.md).

```
komersia_desktop/   paquet Python du lanceur (paths, postgres, setup, server, scheduler, main...)
build/              komersia.spec (PyInstaller), komersia.iss (Inno Setup), build.ps1,
                    komersia-service.ps1 (tâche planifiée, pare-feu, droits), ci-integration-test.ps1,
                    postgres.json (version + SHA-256 du zip PostgreSQL)
tests/              pytest (Linux) : dont le cycle complet réel
```

## Obtenir l'installateur

1. GitHub > Actions > « Installateur Windows » > *Run workflow* (ou pousser une étiquette `v1.0.0`).
2. Première fois : lancer avec « print_pg_hash » coché, copier la somme SHA-256 affichée dans
   `build/postgres.json` (`sha256`), valider, relancer sans la case.
3. Si le test d'intégration réussit, télécharger l'artefact `Komersia-Setup-<version>` (`.exe` + `.sha256`).

## Développement (Linux ou Windows)

```bash
# Tests (depuis la racine du dépôt)
uv run --project backend pytest -c desktop/pytest.ini desktop/tests
uv run --project backend ruff check desktop

# Lancer le lanceur depuis les sources, avec PostgreSQL du système
export KOMERSIA_DATA_DIR=/var/tmp/komersia-data KOMERSIA_PG_BIN=/usr/lib/postgresql/16/bin \
       KOMERSIA_PG_LOCALE=C.UTF-8 PYTHONPATH=desktop
uv run --project backend python -m komersia_desktop.main --run     # en root : KOMERSIA_PG_RUN_AS=postgres
uv run --project backend python -m komersia_desktop.main --status
uv run --project backend python -m komersia_desktop.main --stop
```

Variables : `KOMERSIA_DATA_DIR`, `KOMERSIA_PG_BIN`, `KOMERSIA_PORT`, `KOMERSIA_PG_PORT`, `KOMERSIA_BIND`
(`127.0.0.1` : ce PC seulement), `KOMERSIA_PG_LOCALE`, `KOMERSIA_SCHEDULER=0`, `KOMERSIA_SCHEDULER_DELAY`.
`--manage <commande>` exécute une commande Django (rôle applicatif ; `KOMERSIA_DB_ROLE=owner` pour le propriétaire).

Windows (CI) : PowerShell 5.1, Inno Setup 6.3+ (`choco install innosetup`), `uv`. Les fichiers `.iss` et `.ps1` sont en
UTF-8 **avec BOM** (nécessaire à Inno Setup et à PowerShell 5.1 pour les accents) : ne pas le retirer.
