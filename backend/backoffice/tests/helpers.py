import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from accounts.models import User
from core.tenancy import tenant_context
from sales.models import Sale

# Les données doivent être VALIDÉES (transaction=True) pour être visibles par la connexion `ops`, distincte de
# la connexion `default`. `serialized_rollback` restaure les données de migration (plans) après le nettoyage.
DB = pytest.mark.django_db(transaction=True, serialized_rollback=True, databases=["default", "ops"])

AGENT_PASSWORD = "Agent-Secret-9731!"


def make_agent(phone="+243810000001", name="Agent Un", superuser=False, **extra) -> User:
    return User.objects.create_user(
        phone=phone, password=AGENT_PASSWORD, full_name=name, is_staff=True, is_superuser=superuser, **extra
    )


def agent_client(user=None, **kwargs) -> Client:
    client = Client(**kwargs)
    client.force_login(user or make_agent())
    return client


def make_sale(handle, number, total=1000, currency="CDF", status="completed", age=None) -> Sale:
    with tenant_context(handle.tenant.id):
        sale = Sale.objects.create(
            client_id=uuid.uuid4(), number=number, request_hash="x", status=status, store=handle.store,
            cashier=handle.user, currency=currency, subtotal_minor=total, total_minor=total, paid_minor=total,
            change_currency=currency,
        )
        if age is not None:
            Sale.objects.filter(pk=sale.pk).update(created_at=timezone.now() - age)
    return sale


DAY = timedelta(days=1)
