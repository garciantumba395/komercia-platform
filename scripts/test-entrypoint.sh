#!/bin/bash
# Démarrage de l'image de TEST en ligne (Dockerfile.test) : PostgreSQL local + migrations + démo + gunicorn.
set -euo pipefail

PGBIN=$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)
PGDATA=/var/lib/komersia-pg
rand() { python -c 'import secrets; print(secrets.token_urlsafe(24))'; }

OWNER_PW=$(rand); APP_PW=$(rand); BACKUP_PW=$(rand); OPS_PW=$(rand)
export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-$(rand)}"
export DJANGO_DEBUG=0
export BILLING_PROVIDER="${BILLING_PROVIDER:-sandbox}"   # paiements SIMULÉS pour tester l'abonnement
export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=komersia
export DB_OPS_USER=komersia_ops DB_OPS_PASSWORD="$OPS_PW"

mkdir -p "$PGDATA" && chown postgres:postgres "$PGDATA" && chmod 700 "$PGDATA"
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  gosu postgres "$PGBIN/initdb" -D "$PGDATA" -A scram-sha-256 --pwfile=<(rand) >/dev/null
fi
gosu postgres "$PGBIN/pg_ctl" -D "$PGDATA" -o "-c listen_addresses=127.0.0.1" -w -l /tmp/pg.log start >/dev/null
trap 'gosu postgres "$PGBIN/pg_ctl" -D "$PGDATA" -m fast stop >/dev/null 2>&1 || true' EXIT

# Rôles et base (connexion locale par socket, utilisateur système postgres)
gosu postgres psql -v ON_ERROR_STOP=1 -d postgres -q \
  -v owner_password="$OWNER_PW" -v app_password="$APP_PW" -v backup_password="$BACKUP_PW" \
  -v ops_password="$OPS_PW" -v db_name=komersia -f /opt/komersia/db-init.sql >/dev/null

cd /app
DB_USER=komersia_owner DB_PASSWORD="$OWNER_PW" python manage.py migrate --noinput
export DB_USER=komersia_app DB_PASSWORD="$APP_PW"
python manage.py seed_demo

exec gosu nobody gunicorn komersia.wsgi:application --bind "0.0.0.0:${PORT}" --workers 2 \
  --access-logfile - --error-logfile -
