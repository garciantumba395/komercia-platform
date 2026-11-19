"""Droits PostgreSQL du rôle de la console d'exploitation (`komersia_ops`, alias de base `ops`).

Le rôle est BYPASSRLS (il lit toutes les entreprises) : ses droits SQL sont donc la vraie barrière.
Principe du moindre privilège, table par table :

- lecture seule (SELECT) sur les tables nécessaires à la console ;
- `accounts_user` : SELECT sur des COLONNES seulement, JAMAIS `password` (le hachage est illisible) ;
- UPDATE de la seule colonne `accounts_tenant.is_active` (suspension / réactivation) ;
- INSERT dans `audit_auditentry` (journalisation des actions ; le trigger d'ajout seul interdit le reste) ;
- gestion des fonctions par entreprise (`backoffice_featureflag`).

Rien d'autre : pas de jetons (`authtoken_token`), pas de sessions, pas de paiements bruts de la table
`PaymentReference`, aucune écriture sur les ventes, produits, clients. Il n'y a volontairement PAS de
`ALTER DEFAULT PRIVILEGES` en lecture pour ce rôle : une nouvelle table ne lui est jamais lisible tant qu'on ne
l'ajoute pas ici. Cette liste est dupliquée dans `scripts/db-init.sql` (un test vérifie la parité).
"""

from django.conf import settings

SELECT_TABLES = [
    "accounts_tenant",
    "accounts_membership",
    "accounts_store",
    "billing_plan",
    "billing_subscription",
    "billing_subscriptionpayment",
    "sales_sale",
    "catalog_product",
    "customers_customer",
    "audit_auditentry",
    "backoffice_supportaccessgrant",
    "backoffice_featureflag",
]
USER_COLUMNS = [
    "id", "phone", "email", "full_name", "phone_verified", "is_active", "is_staff", "is_superuser",
    "date_joined", "last_login",
]  # pas `password`


def ops_role_name() -> str:
    return settings.DATABASES["ops"]["USER"]


def grant_statements(role: str) -> list[str]:
    """Instructions GRANT (idempotentes). `role` est un identifiant : il est cité."""
    ident = '"' + role.replace('"', '""') + '"'
    statements = [f'GRANT USAGE ON SCHEMA public TO {ident}']
    statements += [f'GRANT SELECT ON "{table}" TO {ident}' for table in SELECT_TABLES]
    statements += [
        f'GRANT SELECT ({", ".join(USER_COLUMNS)}) ON "accounts_user" TO {ident}',
        f'GRANT UPDATE (is_active) ON "accounts_tenant" TO {ident}',
        f'GRANT INSERT ON "audit_auditentry" TO {ident}',
        f'GRANT INSERT, UPDATE, DELETE ON "backoffice_featureflag" TO {ident}',
    ]
    return statements


def apply_grants(cursor, role: str | None = None) -> bool:
    """Accorde les droits si le rôle existe ; renvoie False sinon (rôle absent : rien à faire)."""
    role = role or ops_role_name()
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
    if cursor.fetchone() is None:
        return False
    for statement in grant_statements(role):
        cursor.execute(statement)
    return True
