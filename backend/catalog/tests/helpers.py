from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Membership, User
from core.tenancy import tenant_context

PASSWORD = "MotDePasse-123!"


def client_for_role(handle, role: str) -> APIClient:
    """Crée un utilisateur de ce rôle dans le tenant et renvoie un client authentifié."""
    if role == "owner":
        return handle.client()
    phone = f"+24398{abs(hash((str(handle.tenant.id), role))) % 10**7:07d}"
    user = User.objects.create_user(phone=phone, password=PASSWORD, full_name=role.title())
    Membership.objects.create(user=user, tenant=handle.tenant, role=role)
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def make_product(handle, **kwargs):
    from catalog.models import Product

    defaults = dict(name="Savon", sku="SAV-1", price_minor=1500, cost_minor=1000, track_stock=True)
    defaults.update(kwargs)
    with tenant_context(handle.tenant.id):
        return Product.objects.create(**defaults)
