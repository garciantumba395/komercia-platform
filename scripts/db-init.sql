-- Initialisation PostgreSQL (rôle propriétaire pour les migrations, rôle applicatif non propriétaire).
-- Exécuter en super-utilisateur : psql -U postgres -f scripts/db-init.sql
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'komersia_owner') THEN
    CREATE ROLE komersia_owner LOGIN PASSWORD 'owner' CREATEDB;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'komersia_app') THEN
    CREATE ROLE komersia_app LOGIN PASSWORD 'app';
  END IF;
END $$;
SELECT 'CREATE DATABASE komersia OWNER komersia_owner'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'komersia')\gexec
\connect komersia
ALTER DEFAULT PRIVILEGES FOR ROLE komersia_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO komersia_app;
ALTER DEFAULT PRIVILEGES FOR ROLE komersia_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO komersia_app;
GRANT USAGE ON SCHEMA public TO komersia_app;
