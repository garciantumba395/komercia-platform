-- Initialisation PostgreSQL : rôle propriétaire (migrations), rôle applicatif non propriétaire (soumis à la RLS),
-- rôle de sauvegarde en lecture seule (BYPASSRLS), rôle de la console d'exploitation (BYPASSRLS, droits minimaux).
-- Idempotent : peut être rejoué sans erreur.
--
-- Usage manuel (valeurs de développement par défaut) :
--   psql -U postgres -f scripts/db-init.sql
-- Avec des valeurs personnalisées (variables psql, toutes facultatives) :
--   psql -U postgres -v owner_password=xxx -v app_password=yyy -v backup_password=zzz -v ops_password=www -v db_name=komersia -f scripts/db-init.sql
-- Sous Docker, voir scripts/docker-initdb.sh (qui lit les variables d'environnement).

-- Valeurs par défaut de DÉVELOPPEMENT uniquement (ne jamais les utiliser en production).
\if :{?owner_password} \else \set owner_password owner \endif
\if :{?app_password} \else \set app_password app \endif
\if :{?backup_password} \else \set backup_password backup \endif
\if :{?ops_password} \else \set ops_password ops \endif
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

-- Rôle de la console d'exploitation /ops/ (personnel Komersia, voir docs/dev/console-exploitation.md) :
-- BYPASSRLS car la console lit l'ensemble des entreprises. Il n'est PAS propriétaire, n'est pas super-utilisateur
-- et n'a que des droits MINIMAUX, accordés table par table (lecture seule ; UPDATE de accounts_tenant.is_active ;
-- INSERT dans audit_auditentry ; gestion de backoffice_featureflag). Les tables n'existant qu'après les migrations,
-- ces droits sont accordés (a) par la migration backoffice.0002 et (b) par le bloc ci-dessous si les tables
-- existent déjà (rejeu sur une base migrée). VOLONTAIREMENT aucun ALTER DEFAULT PRIVILEGES en lecture pour ce
-- rôle : les nouvelles tables (jetons, sessions...) ne doivent jamais lui être lisibles automatiquement.
SELECT format('CREATE ROLE komersia_ops LOGIN PASSWORD %L BYPASSRLS', :'ops_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'komersia_ops')\gexec
ALTER ROLE komersia_ops LOGIN BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

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

-- Console d'exploitation : connexion, usage du schéma et droits minimaux sur les tables existantes.
-- La liste doit rester identique à backend/backoffice/ops_role.py (test de parité dans backoffice/tests).
SELECT format('GRANT CONNECT ON DATABASE %I TO komersia_ops', :'db_name')\gexec
GRANT USAGE ON SCHEMA public TO komersia_ops;
DO $ops$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['accounts_tenant', 'accounts_membership', 'accounts_store', 'billing_plan',
      'billing_subscription', 'billing_subscriptionpayment', 'sales_sale', 'catalog_product',
      'customers_customer', 'audit_auditentry', 'backoffice_supportaccessgrant', 'backoffice_featureflag']
  LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      EXECUTE format('GRANT SELECT ON public.%I TO komersia_ops', t);
    END IF;
  END LOOP;
  IF to_regclass('public.accounts_user') IS NOT NULL THEN
    -- Jamais la colonne `password` (hachage).
    GRANT SELECT (id, phone, email, full_name, phone_verified, is_active, is_staff, is_superuser, date_joined,
                  last_login) ON public.accounts_user TO komersia_ops;
  END IF;
  IF to_regclass('public.accounts_tenant') IS NOT NULL THEN
    GRANT UPDATE (is_active) ON public.accounts_tenant TO komersia_ops;
  END IF;
  IF to_regclass('public.audit_auditentry') IS NOT NULL THEN
    GRANT INSERT ON public.audit_auditentry TO komersia_ops;
  END IF;
  IF to_regclass('public.backoffice_featureflag') IS NOT NULL THEN
    GRANT INSERT, UPDATE, DELETE ON public.backoffice_featureflag TO komersia_ops;
  END IF;
END
$ops$;
