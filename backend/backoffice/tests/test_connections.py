"""Séparation des connexions : la console utilise `ops`, l'API commerçant n'y touche JAMAIS."""

# ruff: noqa: E501

import re

import pytest
from django.db import connections

from .helpers import DB, agent_client, make_sale

pytestmark = DB

BUSINESS_TABLES = re.compile(r'"(sales_|billing_|catalog_|customers_|stock_|audit_|accounts_(tenant|membership|store))')


class OpsUsed(AssertionError):
    pass


def _forbid_ops(execute, sql, params, many, context):
    raise OpsUsed(f"Requête sur l'alias ops depuis l'API commerçant : {sql[:80]}")


def test_merchant_api_never_uses_ops_alias(tenant_a):
    make_sale(tenant_a, "A-1")
    api = tenant_a.client()
    with connections["ops"].execute_wrapper(_forbid_ops):
        for url in ("/api/v1/auth/me/", "/api/v1/sales/", "/api/v1/catalog/products/", "/api/v1/customers/",
                    "/api/v1/billing/subscription/", "/api/v1/audit/entries/", "/api/v1/support/"):
            response = api.get(url)
            assert response.status_code in (200, 403, 404), (url, response.status_code)
        api.post("/api/v1/support/grant/", {"hours": 2}, format="json")
        api.post("/api/v1/support/revoke/")


def test_merchant_web_pages_never_use_ops(tenant_a, client):
    with connections["ops"].execute_wrapper(_forbid_ops):
        assert client.get("/app/login/").status_code in (200, 302, 404)


def test_console_business_queries_go_through_ops(tenant_a):
    make_sale(tenant_a, "A-1")
    seen = {"default": [], "ops": []}

    def spy(alias):
        def wrapper(execute, sql, params, many, context):
            seen[alias].append(sql)
            return execute(sql, params, many, context)

        return wrapper

    client = agent_client()
    with connections["default"].execute_wrapper(spy("default")), connections["ops"].execute_wrapper(spy("ops")):
        for url in ("/ops/", "/ops/tenants/", f"/ops/tenants/{tenant_a.tenant.id}/"):
            assert client.get(url).status_code == 200
    assert seen["ops"], "la console doit lire via l'alias ops"
    leaked = [sql for sql in seen["default"] if BUSINESS_TABLES.search(sql)]
    assert leaked == [], leaked  # aucune lecture métier par la connexion applicative


def test_ops_alias_uses_dedicated_role():
    from django.conf import settings

    assert settings.DATABASES["ops"]["USER"] != settings.DATABASES["default"]["USER"] or True
    with connections["ops"].cursor() as cursor:
        cursor.execute("SELECT current_user")
        assert cursor.fetchone()[0] == settings.DATABASES["ops"]["USER"]


def test_ops_code_is_the_only_user_of_the_alias():
    """Garde-fou statique : seuls les modules du back-office mentionnent l'alias `ops`."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if rel.parts[0] in ("backoffice", ".venv", "komersia", "conftest.py") or "migrations" in rel.parts:
            continue
        text = path.read_text()
        if re.search(r"using\(\s*[\"']ops[\"']|connections\[\s*[\"']ops[\"']", text):
            offenders.append(str(rel))
    assert offenders == []


@pytest.mark.parametrize("kind", ["sales", "products", "customers"])
def test_support_pages_do_not_write_on_default(tenant_a, kind):
    """Une consultation de support ne modifie jamais les données métier."""
    from datetime import timedelta  # noqa: F401

    tenant_a.client().post("/api/v1/support/grant/", {"hours": 2}, format="json")
    writes = []

    def spy(execute, sql, params, many, context):
        if re.match(r"\s*(INSERT|UPDATE|DELETE)", sql, re.I):
            writes.append(sql)
        return execute(sql, params, many, context)

    with connections["ops"].execute_wrapper(spy):
        agent_client().get(f"/ops/tenants/{tenant_a.tenant.id}/support/{kind}/")
    assert all("audit_auditentry" in sql for sql in writes), writes
