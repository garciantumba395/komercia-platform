#!/usr/bin/env bash
# Restauration d'une sauvegarde Komersia (format custom de scripts/backup.sh) vers une base cible.
#
# Usage : scripts/restore.sh <fichier.dump> [base_cible]
#   base_cible : par défaut RESTORE_DB_NAME, sinon "komersia_restore".
#
# Variables d'environnement (toutes facultatives) :
#   DB_HOST (localhost)  DB_PORT (5432)
#   DB_USER (komersia_owner)  DB_PASSWORD     rôle qui crée la base cible et en devient propriétaire
#   RESTORE_DB_NAME                           base cible (alternative au 2e argument)
#   APP_ROLE (komersia_app)                   rôle applicatif à qui rendre les droits après restauration
#   RESTORE_DROP_EXISTING (0)                 1 = supprime la base cible si elle existe déjà
#   PG_RESTORE (pg_restore)  PSQL (psql)
#
# Sécurité : la base cible ne doit PAS exister (sauf RESTORE_DROP_EXISTING=1), et le script refuse
# de restaurer sur la base de travail "komersia" sans confirmation explicite (RESTORE_ALLOW_PRIMARY=1).
# Le rôle DB_USER doit avoir CREATEDB (komersia_owner l'a, voir scripts/db-init.sql).
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage : $0 <fichier.dump> [base_cible]" >&2
  exit 2
fi

FICHIER="$1"
CIBLE="${2:-${RESTORE_DB_NAME:-komersia_restore}}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-komersia_owner}"
APP_ROLE="${APP_ROLE:-komersia_app}"
PG_RESTORE="${PG_RESTORE:-pg_restore}"
PSQL="${PSQL:-psql}"

[ -f "$FICHIER" ] || { echo "Fichier introuvable : $FICHIER" >&2; exit 2; }
case "$CIBLE" in
  *[!A-Za-z0-9_]*|'') echo "Nom de base invalide (lettres, chiffres et _ uniquement) : $CIBLE" >&2; exit 2 ;;
esac
if [ "$CIBLE" = "komersia" ] && [ "${RESTORE_ALLOW_PRIMARY:-0}" != "1" ]; then
  echo "Refus : restaurer sur la base 'komersia' écraserait les données courantes." >&2
  echo "Restaurer vers une autre base, ou définir RESTORE_ALLOW_PRIMARY=1 en connaissance de cause." >&2
  exit 3
fi

if [ -n "${DB_PASSWORD:-}" ]; then
  export PGPASSWORD="$DB_PASSWORD"
fi

psql_admin() { "$PSQL" --host "$DB_HOST" --port "$DB_PORT" --username "$DB_USER" --dbname postgres \
  --no-password -v ON_ERROR_STOP=1 -At "$@"; }

EXISTE="$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = '$CIBLE'")"
if [ -n "$EXISTE" ]; then
  if [ "${RESTORE_DROP_EXISTING:-0}" = "1" ]; then
    echo "Suppression de la base existante $CIBLE (RESTORE_DROP_EXISTING=1)."
    psql_admin -c "DROP DATABASE \"$CIBLE\" WITH (FORCE)"
  else
    echo "La base $CIBLE existe déjà. Choisir un autre nom ou définir RESTORE_DROP_EXISTING=1." >&2
    exit 4
  fi
fi

echo "Création de la base $CIBLE (propriétaire ${DB_USER})..."
psql_admin -c "CREATE DATABASE \"$CIBLE\" OWNER \"$DB_USER\""

echo "Restauration de $FICHIER vers $CIBLE..."
# --no-owner / --no-acl : tout appartient au rôle de restauration (propriétaire), les droits applicatifs
# sont rejoués ci-dessous. Les politiques RLS font partie des objets restaurés.
"$PG_RESTORE" --host "$DB_HOST" --port "$DB_PORT" --username "$DB_USER" --dbname "$CIBLE" \
  --no-password --no-owner --no-acl --exit-on-error "$FICHIER"

# Rejoue les privilèges du rôle applicatif (non propriétaire, soumis à la RLS), si ce rôle existe.
if [ -n "$(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname = '$APP_ROLE'")" ]; then
  "$PSQL" --host "$DB_HOST" --port "$DB_PORT" --username "$DB_USER" --dbname "$CIBLE" --no-password \
    -v ON_ERROR_STOP=1 -q <<SQL
GRANT USAGE ON SCHEMA public TO "$APP_ROLE";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "$APP_ROLE";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "$APP_ROLE";
SQL
  echo "Droits du rôle $APP_ROLE rétablis."
else
  echo "Rôle $APP_ROLE absent : droits applicatifs non rétablis (voir scripts/db-init.sql)."
fi

echo "Restauration terminée : base $CIBLE."
