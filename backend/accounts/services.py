from django.db import transaction
from rest_framework.authtoken.models import Token

from core.permissions import OWNER
from core.tenancy import tenant_context

from .models import Membership, Store, Tenant, User


@transaction.atomic
def register_merchant(*, full_name, phone, password, business_name, email="", sector="retail",
                      base_currency="CDF", address="", store_name=None):
    """Crée l'utilisateur, l'entreprise, la première boutique et le rôle de propriétaire."""
    user = User.objects.create_user(phone=phone, password=password, full_name=full_name,
                                    email=email)
    tenant = Tenant.objects.create(name=business_name, sector=sector,
                                   base_currency=base_currency, address=address)
    Membership.objects.create(user=user, tenant=tenant, role=OWNER)
    with tenant_context(tenant.id):
        store = Store.objects.create(name=store_name or business_name, address=address)
    return user, tenant, store


def issue_token(user) -> str:
    token, _ = Token.objects.get_or_create(user=user)
    return token.key
