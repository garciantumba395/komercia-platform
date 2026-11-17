-- Initialisation PostgreSQL : rôle propriétaire (migrations), rôle applicatif non propriétaire (soumis à la RLS),
-- rôle de sauvegarde en lecture seule (BYPASSRLS).
-- Idempotent : peut être rejoué sans erreur.
--
-- Usage manuel (valeurs de développement par défaut) :
--   psql -U postgres -f scripts/db-init.sql
-- Avec des valeurs personnalisées (variables psql, toutes facultatives) :
--   psql -U postgres -v owner_password=xxx -v app_password=yyy -v backup_password=zzz -v db_name=komersia -f scripts/db-init.sql
-- Sous Docker, voir scripts/docker-initdb.sh (qui lit les variables d'environnement).

-- Valeurs par défaut de DÉVELOPPEMENT uniquement (ne jamais les utiliser en production).
\if :{?owner_password} \else \set owner_password owner \endif
\if :{?app_password} \else \set app_password app \endif
\if :{?backup_password} \else \set backup_password backup \endif
\if :{?db_name} \else \set db_name komersia \endif

SELECT format('CREATE ROLE komersia_owner LOGIN PASSWORD %L CREATEDB', :'owner_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'komersia_owner')\gexec
SELECT format('CREATE ROLE komersia_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'komersia_app')\gexec

-- Rôle de sauvegarde (scripts/backup.sh) : lecture seule MAIS BYPASSRLS. Indispensable : les tables
-- utilisent FORCE ROW LEVEL SECURITY, donc même le propriétaire ne peut pas tout lire pour pg_dump.
SELECT format('CREATE ROLE komersia_backup LOGIN PASSWORD %L BYPASSRLS', :'backup_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'komersia_backup')\gexec
GRANT pg_read_all_data TO komersia_backup;

SELECT format('CREATE DATABASE %I OWNER komersia_owner', :'db_name')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db_name')\gexec
-- Si la base existait déjà (ex. créée par l'image Docker avec POSTGRES_DB), elle passe au rôle propriétaire.
SELECT format('ALTER DATABASE %I OWNER TO komersia_owner', :'db_name')\gexec

\connect :db_name
ALTER DEFAULT PRIVILEGES FOR ROLE komersia_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO komersia_app;
ALTER DEFAULT PRIVILEGES FOR ROLE komersia_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO komersia_app;
GRANT USAGE ON SCHEMA public TO komersia_app;
