#!/bin/sh
# Exécuté automatiquement par l'image postgres au premier démarrage (docker-entrypoint-initdb.d).
# Transmet les mots de passe des rôles à scripts/db-init.sql (monté dans /opt/komersia/db-init.sql).
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_password="${KOMERSIA_OWNER_PASSWORD:?KOMERSIA_OWNER_PASSWORD manquant}" \
  -v app_password="${KOMERSIA_APP_PASSWORD:?KOMERSIA_APP_PASSWORD manquant}" \
  -v backup_password="${KOMERSIA_BACKUP_PASSWORD:?KOMERSIA_BACKUP_PASSWORD manquant}" \
  -v db_name="${POSTGRES_DB:-komersia}" \
  -f /opt/komersia/db-init.sql
