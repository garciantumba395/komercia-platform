"""Concurrence réelle (threads, transactions validées) : idempotence et numérotation."""

import threading
import uuid

import pytest
from django.db import connection

from core.tenancy import tenant_context
from sales.models import Sale
from sales.tests.helpers import BASE, cash, make_product, sale_body
from stock.services import apply_movement, get_quantity


def run_parallel(handle, bodies):
    results = [None] * len(bodies)
    barrier = threading.Barrier(len(bodies))

    def work(i):
        try:
            client = handle.client()
            barrier.wait()
            results[i] = client.post(BASE, bodies[i], format="json")
        except Exception as exc:  # pragma: no cover
            results[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(len(bodies))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


@pytest.mark.django_db(transaction=True)
def test_concurrent_same_client_id_creates_one_sale(tenant_a):
    p = make_product(tenant_a, price_minor=1000)
    with tenant_context(tenant_a.tenant.id):
        apply_movement(p, tenant_a.store, 10, "in")
    body = sale_body(tenant_a, [(p, 2)], [cash(2000)])
    results = run_parallel(tenant_a, [body] * 6)
    assert not [r for r in results if isinstance(r, Exception)], results
    codes = sorted(r.status_code for r in results)
    assert codes == [200] * 5 + [201], codes
    assert len({r.data["id"] for r in results}) == 1
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 1
        assert get_quantity(p, tenant_a.store) == 8


@pytest.mark.django_db(transaction=True)
def test_concurrent_distinct_sales_get_unique_gapless_numbers(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    bodies = [{**sale_body(tenant_a, [(p, 1)], [cash(1000)]), "client_id": str(uuid.uuid4())}
              for _ in range(8)]
    results = run_parallel(tenant_a, bodies)
    assert all(not isinstance(r, Exception) and r.status_code == 201 for r in results), results
    numbers = sorted(r.data["number"] for r in results)
    assert numbers == [f"B01-{i:06d}" for i in range(1, 9)]
