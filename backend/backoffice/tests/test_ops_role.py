"""Le rôle PostgreSQL `komersia_ops` : BYPASSRLS, droits minimaux, et non-régression de l'isolation."""

import re
from pathlib import Path

import pytest
from django.db import ProgrammingError, connections, transaction
from django.db.utils import InternalError, OperationalError

from accounts.models import Tenant
from audit.models import AuditEntry
from backoffice import ops_role
from sales.models import Sale

from .helpers import DB, make_sale

pytestmark = DB


def _tenant_pair(tenant_a, tenant_b):
    make_sale(tenant_a, "A-1")
    make_sale(tenant_b, "B-1")
    make_sale(tenant_b, "B-2")


def test_role_attributes():
    with connections["ops"].cursor() as cursor:
        cursor.execute("SELECT current_user, rolbypassrls, rolsuper, rolcreatedb, rolcreaterole "
                       "FROM pg_roles WHERE rolname = current_user")
        user, bypass, superuser, createdb, createrole = cursor.fetchone()
    assert user == ops_role.ops_role_name()
    assert bypass is True
    assert not (superuser or createdb or createrole)


def test_ops_sees_all_tenants_but_rls_hides_them_without_bypass(tenant_a, tenant_b):
    """Sans BYPASSRLS (connexion `default`, sans tenant actif) : 0 ligne ; avec (`ops`) : tout."""
    _tenant_pair(tenant_a, tenant_b)
    assert Sale.objects.using("default").count() == 0  # RLS FORCE : aucune ligne sans contexte de tenant
    assert Sale.objects.using("ops").count() == 3
    assert set(Sale.objects.using("ops").values_list("tenant_id", flat=True)) == {tenant_a.tenant.id, tenant_b.tenant.id}


def test_ordinary_roles_still_isolated(tenant_a, tenant_b):
    """Non-régression : un tenant ne voit toujours que ses propres ventes."""
    from core.tenancy import tenant_context

    _tenant_pair(tenant_a, tenant_b)
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 1
    with tenant_context(tenant_b.tenant.id):
        assert Sale.objects.count() == 2


@pytest.mark.parametrize("statement", [
    "UPDATE accounts_tenant SET name = 'x'",
    "UPDATE sales_sale SET number = 'x'",
    "DELETE FROM sales_sale",
    "DELETE FROM accounts_tenant",
    "SELECT key FROM authtoken_token",
    "SELECT password FROM accounts_user",
    "SELECT * FROM accounts_user",
    "SELECT * FROM django_session",
    "SELECT * FROM billing_paymentreference",
    "UPDATE audit_auditentry SET reason = 'x'",
    "DELETE FROM audit_auditentry",
    "INSERT INTO accounts_tenant (id, name) VALUES (gen_random_uuid(), 'x')",
    "UPDATE backoffice_supportaccessgrant SET revoked_at = now()",
    "INSERT INTO sales_sale (id) VALUES (gen_random_uuid())",
])
def test_ops_least_privilege(tenant_a, statement):
    make_sale(tenant_a, "A-1")
    with pytest.raises((ProgrammingError, OperationalError, InternalError)) as exc:
        with connections["ops"].cursor() as cursor:
            cursor.execute(statement)
    assert "permission denied" in str(exc.value) or "droit" in str(exc.value).lower()


def test_ops_allowed_writes(tenant_a):
    Tenant.objects.using("ops").filter(pk=tenant_a.tenant.id).update(is_active=False)
    assert not Tenant.objects.get(pk=tenant_a.tenant.id).is_active
    AuditEntry(tenant_id=tenant_a.tenant.id, action="platform.test", entity_type="tenant",
               entity_id="x").save(using="ops")
    from core.tenancy import tenant_context

    with tenant_context(tenant_a.tenant.id):
        assert AuditEntry.objects.filter(action="platform.test").count() == 1


def test_ops_cannot_alter_audit_even_with_grants(tenant_a):
    entry = AuditEntry(tenant_id=tenant_a.tenant.id, action="platform.test", entity_type="tenant", entity_id="x")
    entry.save(using="ops")
    with pytest.raises((ProgrammingError, InternalError, OperationalError)):
        with transaction.atomic(using="ops"), connections["ops"].cursor() as cursor:
            cursor.execute("DELETE FROM audit_auditentry")


def test_app_role_privileges_on_backoffice_tables_match_other_global_tables():
    """Les tables du backoffice (sans RLS) sont accessibles au rôle applicatif comme les autres tables globales
    (privilèges par défaut de scripts/db-init.sql). Ignoré si le rôle applicatif n'a aucun droit sur cette base
    (base de test : les privilèges par défaut n'y sont pas définis)."""
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'komersia_app'")
        if cursor.fetchone() is None:
            pytest.skip("rôle komersia_app absent")
        cursor.execute("SELECT has_table_privilege('komersia_app', 'billing_plan', 'SELECT')")
        if not cursor.fetchone()[0]:
            pytest.skip("privilèges par défaut du rôle applicatif non définis sur cette base")
        for table in ("backoffice_supportaccessgrant", "backoffice_featureflag"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cursor.execute("SELECT has_table_privilege('komersia_app', %s, %s)", [table, privilege])
                assert cursor.fetchone()[0], (table, privilege)


def test_db_init_sql_lists_the_same_tables():
    sql = (Path(__file__).resolve().parents[3] / "scripts" / "db-init.sql").read_text()
    in_sql = set(re.findall(r"'((?:accounts|billing|sales|catalog|customers|audit|backoffice)_[a-z]+)'", sql))
    assert in_sql == set(ops_role.SELECT_TABLES)
    for column in ops_role.USER_COLUMNS:
        assert column in sql
    assert "password" not in ops_role.USER_COLUMNS
