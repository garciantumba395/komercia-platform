"""Tests d'isolation multi-tenant (KOM-NF-008) : critiques, ils bloquent le merge."""

import pytest
from django.db import ProgrammingError, connection, transaction

from accounts.models import Store
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def test_api_never_returns_other_tenant_data(tenant_a, tenant_b):
    names_a = {s["name"] for s in tenant_a.client().get("/api/v1/stores/").data["results"]}
    names_b = {s["name"] for s in tenant_b.client().get("/api/v1/stores/").data["results"]}
    assert names_a == {"Commerce A"} and names_b == {"Commerce B"}


def test_cannot_read_or_modify_other_tenant_object_by_id(tenant_a, tenant_b):
    api = tenant_a.client()
    other = tenant_b.store.id
    assert api.get(f"/api/v1/stores/{other}/").status_code == 404
    assert api.patch(f"/api/v1/stores/{other}/", {"name": "pirate"}, format="json").status_code == 404
    assert api.delete(f"/api/v1/stores/{other}/").status_code == 404
    with tenant_context(tenant_b.tenant.id):
        assert Store.objects.get(id=other).name == "Commerce B"


def test_database_filters_rows_without_any_application_filter(tenant_a, tenant_b):
    with tenant_context(tenant_a.tenant.id):
        assert set(Store.objects.values_list("name", flat=True)) == {"Commerce A"}
    with tenant_context(tenant_b.tenant.id):
        assert set(Store.objects.values_list("name", flat=True)) == {"Commerce B"}


def test_no_tenant_context_returns_no_rows(tenant_a, tenant_b):
    with transaction.atomic():
        with connection.cursor() as c:
            c.execute("SELECT count(*) FROM accounts_store")
            assert c.fetchone()[0] == 0


def test_cannot_insert_row_for_another_tenant(tenant_a, tenant_b):
    with tenant_context(tenant_a.tenant.id):
        with pytest.raises(ProgrammingError, match="row-level security"):
            with transaction.atomic():
                Store.objects.create(name="Intrus", tenant_id=tenant_b.tenant.id)


def test_tenant_id_cannot_be_forced_by_client(tenant_a, tenant_b):
    res = tenant_a.client().post(
        "/api/v1/stores/", {"name": "Nouveau", "tenant": str(tenant_b.tenant.id)}, format="json"
    )
    assert res.status_code == 201
    with tenant_context(tenant_b.tenant.id):
        assert not Store.objects.filter(name="Nouveau").exists()
    with tenant_context(tenant_a.tenant.id):
        assert Store.objects.filter(name="Nouveau").exists()
