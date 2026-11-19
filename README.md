# komercia-platform

**Komersia** : SaaS multi-tenant de gestion commerciale pour les commerçants de la RDC (caisse, stock, clients et
crédit, rapports), avec isolation stricte des données entre commerçants (PostgreSQL Row-Level Security).

- Cahier des charges : [`docs/product/cahier-des-charges.md`](docs/product/cahier-des-charges.md)
- Choix de la stack : [`docs/adr/0001-choix-de-la-stack.md`](docs/adr/0001-choix-de-la-stack.md)
- Sauvegarde et restauration : [`docs/dev/sauvegarde-restauration.md`](docs/dev/sauvegarde-restauration.md)
- Guide du backend (règles multi-tenant, modules) : [`backend/README.md`](backend/README.md)

## Structure du dépôt

```
backend/                 Django 5 + DRF, monolithe modulaire (une application par module métier)
  komersia/              paramètres, URL, WSGI
  core, accounts, catalog, stock, customers, currency, sales, reports, audit
  Dockerfile             image Python 3.13 (uv, gunicorn, utilisateur non-root)
docs/
  adr/                   décisions d'architecture
  product/               cahier des charges
  dev/                   documentation d'exploitation (sauvegarde/restauration)
scripts/
  db-init.sql            création des rôles (propriétaire, applicatif, sauvegarde) et de la base
  docker-initdb.sh       adaptateur utilisé par le conteneur PostgreSQL
  backup.sh, restore.sh  sauvegarde et restauration pg_dump / pg_restore
docker-compose.yml       db + migrate + web
.env.example             variables d'environnement (valeurs factices de développement)
.github/workflows/ci.yml intégration continue
```

## Démarrage rapide avec Docker

Prérequis : Docker et Docker Compose v2.

```bash
cp .env.example .env          # valeurs factices de développement : à adapter, ne jamais réutiliser en production
docker compose up --build
# db démarre -> migrate applique les migrations (rôle propriétaire) -> web sert l'API (rôle applicatif)
# API : http://localhost:8000/api/docs/
```

Le PostgreSQL du conteneur est publié sur `localhost:5433`. Les rôles sont créés au **premier** démarrage (volume vide) ;
pour repartir de zéro : `docker compose down -v`.

## Démarrage rapide sans Docker

Prérequis : Python 3.12+ (3.13 conseillé), [uv](https://docs.astral.sh/uv/), PostgreSQL 16.

```bash
# 1. Rôles et base (une fois), en super-utilisateur PostgreSQL
psql -U postgres -f scripts/db-init.sql
cd backend
# 2. Dépendances
uv sync
# 3. Migrations avec le rôle PROPRIÉTAIRE
DB_USER=komersia_owner DB_PASSWORD=owner uv run python manage.py migrate
# 4. Serveur avec le rôle APPLICATIF (soumis à la RLS)
uv run python manage.py runserver
```

Les mots de passe par défaut de `db-init.sql` (`owner`, `app`, `backup`) sont des valeurs de développement ; on peut les
remplacer avec `psql -v owner_password=... -v app_password=... -v backup_password=...`.

## Tests et qualité

```bash
cd backend
DB_USER=komersia_owner DB_PASSWORD=owner uv run pytest
DB_USER=komersia_owner DB_PASSWORD=owner uv run pytest -k isolation   # tests d'isolation multi-tenant (bloquants)
uv run ruff check .
```

Plusieurs personnes ou agents en parallèle : une base de test distincte par personne
(`DB_NAME=komersia_monmodule ... pytest --create-db`). Détails et règles dans [`backend/README.md`](backend/README.md).

## Intégration continue

`.github/workflows/ci.yml` (push et pull request) : PostgreSQL 16, création des rôles, `uv sync`, `ruff check`,
migrations avec le rôle propriétaire, vérification que les tests d'isolation existent puis tests (isolation, puis suite
complète). Le déploiement vers l'environnement de test n'est pas encore en place (TODO dans le fichier).

## Sauvegarde et restauration

```bash
BACKUP_DB_PASSWORD=... ./scripts/backup.sh                                  # pg_dump horodaté + rotation
RESTORE_DB_PASSWORD=... ./scripts/restore.sh backups/<fichier>.dump komersia_restore
```

Procédure, objectifs RPO/RTO et test trimestriel : [`docs/dev/sauvegarde-restauration.md`](docs/dev/sauvegarde-restauration.md).
