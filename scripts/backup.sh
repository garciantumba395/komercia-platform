#!/usr/bin/env bash
# Sauvegarde PostgreSQL de Komersia : pg_dump au format custom, horodaté, avec rotation.
#
# Variables d'environnement (toutes facultatives) :
#   DB_HOST (localhost)  DB_PORT (5432)  DB_NAME (komersia)
#   BACKUP_DB_USER (komersia_backup)  BACKUP_DB_PASSWORD (transmis à pg_dump via PGPASSWORD)
#   BACKUP_DIR (./backups)            dossier de destination
#   BACKUP_RETENTION_DAYS (14)        supprime les sauvegardes plus anciennes ; 0 = pas de rotation
#   BACKUP_PREFIX (komersia)          préfixe des fichiers
#   PG_DUMP (pg_dump)                 commande à utiliser
#
# IMPORTANT : les tables métier utilisent FORCE ROW LEVEL SECURITY, qui s'applique même au rôle
# propriétaire : pg_dump échoue (ou produirait une sauvegarde vide) avec komersia_owner ou komersia_app.
# Le rôle de sauvegarde doit donc avoir l'attribut BYPASSRLS : c'est le rôle dédié `komersia_backup`
# créé par scripts/db-init.sql (lecture seule : pg_read_all_data). Un super-utilisateur convient aussi.
# DB_HOST peut être vide pour utiliser le socket Unix local (authentification « peer »).
#
# Exemple : BACKUP_DB_PASSWORD=... BACKUP_DIR=/var/backups/komersia ./scripts/backup.sh
set -euo pipefail

DB_HOST="${DB_HOST-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-komersia}"
BACKUP_DB_USER="${BACKUP_DB_USER:-komersia_backup}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_PREFIX="${BACKUP_PREFIX:-komersia}"
PG_DUMP="${PG_DUMP:-pg_dump}"

case "$BACKUP_RETENTION_DAYS" in
  ''|*[!0-9]*) echo "BACKUP_RETENTION_DAYS doit être un entier positif ou nul." >&2; exit 2 ;;
esac

if [ -n "${BACKUP_DB_PASSWORD:-}" ]; then
  export PGPASSWORD="$BACKUP_DB_PASSWORD"
fi
HOTE=()
if [ -n "$DB_HOST" ]; then HOTE=(--host "$DB_HOST"); fi

mkdir -p "$BACKUP_DIR"
umask 077

HORODATAGE="$(date -u +%Y%m%dT%H%M%SZ)"
FICHIER="$BACKUP_DIR/${BACKUP_PREFIX}_${DB_NAME}_${HORODATAGE}.dump"
TEMPORAIRE="$FICHIER.partial"

# Nettoyage du fichier partiel en cas d'échec : on ne laisse jamais une sauvegarde incomplète.
trap 'rm -f "$TEMPORAIRE"' EXIT

echo "Sauvegarde de ${DB_NAME} sur ${DB_HOST}:${DB_PORT} (utilisateur ${BACKUP_DB_USER})..."
"$PG_DUMP" "${HOTE[@]}" --port "$DB_PORT" --username "$BACKUP_DB_USER" --dbname "$DB_NAME" \
  --format=custom --compress=6 --no-password --file "$TEMPORAIRE"

# Vérification : l'archive doit être lisible par pg_restore.
if command -v "${PG_RESTORE:-pg_restore}" >/dev/null 2>&1; then
  "${PG_RESTORE:-pg_restore}" --list "$TEMPORAIRE" >/dev/null
fi

mv "$TEMPORAIRE" "$FICHIER"
trap - EXIT
echo "Sauvegarde créée : $FICHIER ($(du -h "$FICHIER" | cut -f1))"

# Rotation : suppression des sauvegardes de ce préfixe plus anciennes que N jours.
if [ "$BACKUP_RETENTION_DAYS" -gt 0 ]; then
  find "$BACKUP_DIR" -maxdepth 1 -type f -name "${BACKUP_PREFIX}_${DB_NAME}_*.dump" \
    -mtime "+$BACKUP_RETENTION_DAYS" -print -delete | sed 's/^/Rotation, supprimé : /'
fi
