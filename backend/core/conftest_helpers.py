"""Fixtures communes de tests multi-tenant, utilisées par tous les modules (voir backend/conftest.py)."""

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.services import register_merchant


class TenantHandle:
    def __init__(self, user, tenant, store, token):
        self.user, self.tenant, self.store, self.token = user, tenant, store, token

    def client(self) -> APIClient:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        return client


def make_tenant(name="Boutique", phone="+243990000001", role_users=None) -> TenantHandle:
    user, tenant, store = register_merchant(
        full_name=f"Patron {name}", phone=phone, password="MotDePasse-123!", business_name=name
    )
    token, _ = Token.objects.get_or_create(user=user)
    return TenantHandle(user, tenant, store, token)


@pytest.fixture
def tenant_a(db):
    return make_tenant("Commerce A", "+243990000001")


@pytest.fixture
def tenant_b(db):
    return make_tenant("Commerce B", "+243990000002")
