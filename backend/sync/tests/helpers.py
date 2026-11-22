import gzip
import json
import uuid
from datetime import timedelta

from django.utils import timezone

from core.tenancy import tenant_context
from sales.tests.helpers import cash, client_for_role, make_customer, make_product, make_rate  # noqa: F401
from stock.services import apply_movement

PUSH = "/api/v1/sync/push/"
PULL = "/api/v1/sync/pull/"
STATUS = "/api/v1/sync/status/"
DEVICE = str(uuid.uuid4())


def op(type_, payload, *, op_id=None, device_time=None):
    return {
        "op_id": str(op_id or uuid.uuid4()),
        "type": type_,
        "device_time": (device_time or timezone.now()).isoformat(),
        "payload": payload,
    }


def sale_op(handle, product, qty=1, *, payments=None, op_id=None, device_time=None, **extra):
    payload = {
        "client_id": str(uuid.uuid4()),
        "store": str(handle.store.id),
        "items": [{"product": str(product.id), "quantity": str(qty)}],
        "payments": payments if payments is not None else [cash(int(product.price_minor * qty))],
    }
    payload.update(extra)
    return op("sale.create", payload, op_id=op_id, device_time=device_time)


def push(client, operations, device_id=DEVICE):
    return client.post(PUSH, {"device_id": device_id, "operations": operations}, format="json")


def results(response):
    assert response.status_code == 200, response.content
    return response.json()["results"]


def stock(handle, product, qty):
    with tenant_context(handle.tenant.id):
        apply_movement(product, handle.store, qty, "in")


def pull(client, **params):
    return client.get(PULL, params)


def body(response):
    """Corps JSON d'une réponse `pull` (décompresse le gzip si besoin)."""
    raw = response.content
    if response.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def minutes_ago(n):
    return timezone.now() - timedelta(minutes=n)
