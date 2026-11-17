import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Membership, User


@pytest.fixture
def client_for_role():
    """Fabrique un client authentifié ayant le rôle donné dans le tenant donné."""

    def make(handle, role, phone):
        user = User.objects.create_user(phone=phone, password="MotDePasse-123!", full_name=role)
        Membership.objects.create(user=user, tenant=handle.tenant, role=role)
        token, _ = Token.objects.get_or_create(user=user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        return client

    return make
