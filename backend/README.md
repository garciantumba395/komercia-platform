# Backend Komersia

Django 5 + Django REST Framework + PostgreSQL (Row-Level Security). Voir `../docs/adr/0001-choix-de-la-stack.md`
et le cahier des charges `../docs/product/cahier-des-charges.md`.

## Démarrer (développement)

```bash
# 1. PostgreSQL : créer les rôles et la base (une fois), en super-utilisateur
psql -U postgres -f ../scripts/db-init.sql
# 2. Dépendances
uv sync
# 3. Migrations avec le rôle PROPRIÉTAIRE
DB_USER=komersia_owner DB_PASSWORD=owner uv run python manage.py migrate
# 4. Lancer le serveur avec le rôle APPLICATIF (soumis à la RLS)
uv run python manage.py runserver
# Documentation de l'API : http://localhost:8000/api/docs/
```

## Tests

```bash
DB_USER=komersia_owner DB_PASSWORD=owner uv run pytest
# Plusieurs personnes ou agents en parallèle : une base de test distincte par personne
DB_USER=komersia_owner DB_PASSWORD=owner DB_NAME=komersia_monmodule uv run pytest --create-db monapp/
uv run ruff check .
```

## Règles du multi-tenant (OBLIGATOIRES)

1. Tout modèle métier hérite de `core.models.TenantOwnedModel` (colonne `tenant`, UUID, dates). Le tenant est
   renseigné automatiquement à l'enregistrement depuis le contexte ; ne jamais le lire d'un paramètre client.
2. La migration qui crée la table doit contenir `core.rls.EnableTenantRLS("<nom_de_table>")` en dernière opération
   (exemple : `accounts/migrations/0001_initial.py`). Sans cela, la table n'est PAS isolée.
3. Les vues DRF utilisent l'authentification par défaut (`TenantTokenAuthentication`) : le tenant est activé
   par requête et PostgreSQL filtre les lignes. Les requêtes ORM n'ont donc pas besoin de `.filter(tenant=...)`.
4. Permissions : `permission_classes = [HasRole]` avec `allowed_roles` ou `allowed_roles_by_action`
   (`core.permissions`, matrice section 19 du cahier des charges). Refus par défaut.
5. Hors requête HTTP (tests, scripts, tâches) : `with core.tenancy.tenant_context(tenant_id): ...`.
6. Les montants sont des **entiers** dans la plus petite unité de la devise (CDF : franc ; USD : centime),
   toujours accompagnés d'un code de devise. Jamais de `float`.
7. Chaque module a des tests, dont au moins un test d'isolation (tenant A ne voit pas tenant B).
   Fixtures disponibles : `tenant_a`, `tenant_b` (avec `.client()`, `.tenant`, `.store`, `.user`),
   voir `core/conftest_helpers.py`.
8. Les routes d'API sont sous `/api/v1/<module>/`, documentées par drf-spectacular.
9. Les écritures sensibles (annulation, ajustement de stock, changement de prix) appelleront `audit.services.log`
   dès que le module `audit` sera disponible.

## Modules

| Module | Rôle |
|---|---|
| `core` | Contexte de tenant, RLS, permissions, base des modèles |
| `accounts` | Utilisateurs, entreprises (tenants), boutiques, rôles, authentification |
| `catalog` | Produits, services, catégories |
| `stock` | Niveaux et mouvements de stock |
| `customers` | Clients, crédit et dettes |
| `currency` | Devises, taux de change, arrondis |
| `sales` | Caisse, ventes, paiements, sessions de caisse |
| `reports` | Rapports de base |
| `audit` | Journal d'audit |
