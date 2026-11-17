"""Tests du `push` : idempotence, rejets isolés, conflits, avertissements, permissions, isolation."""

import threading
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from audit.models import AuditEntry
from core.tenancy import tenant_context
from customers.models import Customer
from sales.models import CashSession, Sale
from stock.models import StockMovement
from stock.services import get_quantity
from sync.models import AppendOnlyError, SyncOperation
from sync.tests.helpers import (
    DEVICE,
    PUSH,
    cash,
    client_for_role,
    make_customer,
    make_product,
    minutes_ago,
    op,
    push,
    results,
    sale_op,
    stock,
)

# --------------------------------------------------------------------------- idempotence


def test_sale_applied_then_duplicate(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 10)
    c = tenant_a.client()
    operation = sale_op(tenant_a, p, 2)
    first = results(push(c, [operation]))[0]
    assert first["status"] == "applied"
    assert first["data"]["number"].startswith("B01-")
    second = results(push(c, [operation]))[0]
    assert second["status"] == "duplicate"
    assert second["entity_id"] == first["entity_id"]
    assert second["data"] == first["data"]
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 1
        assert get_quantity(p, tenant_a.store) == 8
        sale = Sale.objects.get()
        assert str(sale.id) == first["entity_id"]


def test_same_client_id_other_op_id_is_duplicate_not_second_sale(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 10)
    c = tenant_a.client()
    first = sale_op(tenant_a, p)
    again = sale_op(tenant_a, p)
    again["payload"] = first["payload"]  # même vente, op_id différent
    r1 = results(push(c, [first]))[0]
    r2 = results(push(c, [again]))[0]
    assert r2["status"] == "duplicate" and r2["entity_id"] == r1["entity_id"]
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 1
        assert get_quantity(p, tenant_a.store) == 9


def test_same_client_id_different_content_is_rejected_conflict(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 10)
    c = tenant_a.client()
    first = sale_op(tenant_a, p, 1)
    other = sale_op(tenant_a, p, 2)
    other["payload"]["client_id"] = first["payload"]["client_id"]
    results(push(c, [first]))
    r = results(push(c, [other]))[0]
    assert r["status"] == "rejected" and r["error"]["code"] == "conflict"
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 1


def test_thousand_sales_in_batches_then_full_replay(tenant_a):
    p = make_product(tenant_a, price_minor=500, sku="P1")
    stock(tenant_a, p, 5000)
    c = tenant_a.client()
    operations = [sale_op(tenant_a, p, 1) for _ in range(1000)]
    for i in range(0, 1000, 200):
        res = results(push(c, operations[i:i + 200]))
        assert [r["status"] for r in res] == ["applied"] * 200
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 1000
        assert get_quantity(p, tenant_a.store) == 4000
        assert StockMovement.objects.filter(kind="sale").count() == 1000
        numbers = set(Sale.objects.values_list("number", flat=True))
        assert len(numbers) == 1000
    # Rejeu complet du même lot (réponses supposées perdues) : rien en plus.
    entity_by_op = {}
    for i in range(0, 1000, 200):
        for operation, r in zip(operations[i:i + 200], results(push(c, operations[i:i + 200])), strict=True):
            assert r["status"] == "duplicate"
            assert r["op_id"] == operation["op_id"]
            entity_by_op[r["op_id"]] = r["entity_id"]
    assert len(set(entity_by_op.values())) == 1000
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 1000
        assert get_quantity(p, tenant_a.store) == 4000
        assert SyncOperation.objects.filter(status="applied").count() == 1000


@pytest.mark.django_db(transaction=True)
def test_concurrent_pushes_same_op_ids_no_duplicate(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 100)
    operations = [sale_op(tenant_a, p, 1) for _ in range(12)]
    customer_op = op("customer.create", {"id": str(uuid.uuid4()), "name": "Parallèle"})
    operations.insert(3, customer_op)
    out = [None] * 4
    barrier = threading.Barrier(4)

    def work(i):
        try:
            client = tenant_a.client()
            barrier.wait()
            out[i] = push(client, operations)
        except Exception as exc:  # pragma: no cover
            out[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not [r for r in out if isinstance(r, Exception)], out
    all_results = [results(r) for r in out]
    for res in all_results:  # chaque opération a un résultat dans chaque réponse
        assert [r["op_id"] for r in res] == [o["op_id"] for o in operations]
        assert all(r["status"] in ("applied", "duplicate") for r in res)
    for i, operation in enumerate(operations):  # une seule réponse a "applied", même entité partout
        statuses = sorted(res[i]["status"] for res in all_results)
        assert statuses == ["applied", "duplicate", "duplicate", "duplicate"], statuses
        assert len({res[i]["entity_id"] for res in all_results}) == 1
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 12
        assert Customer.objects.filter(name="Parallèle").count() == 1
        assert get_quantity(p, tenant_a.store) == 88
        assert SyncOperation.objects.filter(status="applied").count() == 13


@pytest.mark.django_db(transaction=True)
def test_lost_response_then_replay(tenant_a):
    """Le lot est appliqué, la réponse est perdue (ignorée) : le rejeu ne refait rien."""
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 20)
    customer_id = str(uuid.uuid4())
    operations = [
        op("customer.create", {"id": customer_id, "name": "Mado"}),
        sale_op(tenant_a, p, 2, customer=customer_id, payments=[cash(500)], credit_remaining=True),
        sale_op(tenant_a, p, 1),
    ]
    c = tenant_a.client()
    push(c, operations)  # réponse perdue
    replay = results(push(c, operations))
    assert [r["status"] for r in replay] == ["duplicate"] * 3
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 2
        assert Customer.objects.count() == 1
        assert get_quantity(p, tenant_a.store) == 17


# --------------------------------------------------------------------------- rejets isolés


def test_rejected_in_the_middle_does_not_block_others(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 10)
    inactive = make_product(tenant_a, price_minor=1000, sku="P2", is_active=False)
    good1, bad, good2 = sale_op(tenant_a, p), sale_op(tenant_a, inactive), sale_op(tenant_a, p)
    res = results(push(tenant_a.client(), [good1, bad, good2]))
    assert [r["status"] for r in res] == ["applied", "rejected", "applied"]
    assert res[1]["error"]["code"] == "product_inactive"
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 2
        assert get_quantity(p, tenant_a.store) == 8
        # La tentative rejetée est journalisée avec son contenu : rien n'est perdu.
        rejected = SyncOperation.objects.get(status="rejected")
        assert str(rejected.op_id) == bad["op_id"]
        assert rejected.payload["client_id"] == bad["payload"]["client_id"]


def test_unknown_product_and_store(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    unknown = sale_op(tenant_a, p)
    unknown["payload"]["items"][0]["product"] = str(uuid.uuid4())
    bad_store = sale_op(tenant_a, p)
    bad_store["payload"]["store"] = str(uuid.uuid4())
    res = results(push(tenant_a.client(), [unknown, bad_store]))
    assert res[0]["error"]["code"] == "product_not_found"
    assert res[1]["error"]["code"] == "store_not_found"


def test_validation_error_and_unknown_type_are_per_operation(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 5)
    res = results(push(tenant_a.client(), [
        op("sale.create", {"client_id": "pas-un-uuid"}),
        op("inventory.magic", {}),
        sale_op(tenant_a, p),
    ]))
    assert [r["status"] for r in res] == ["rejected", "rejected", "applied"]
    assert res[0]["error"]["code"] == "validation_error"
    assert res[1]["error"]["code"] == "unknown_type"


def test_rejected_operation_is_reevaluated_on_resend(tenant_a):
    """Un rejet n'est pas définitif : le produit réactivé, le même op_id s'applique."""
    p = make_product(tenant_a, price_minor=1000, sku="P1", is_active=False)
    operation = sale_op(tenant_a, p)
    c = tenant_a.client()
    assert results(push(c, [operation]))[0]["status"] == "rejected"
    with tenant_context(tenant_a.tenant.id):
        p.is_active = True
        p.save()
    assert results(push(c, [operation]))[0]["status"] == "applied"
    assert results(push(c, [operation]))[0]["status"] == "duplicate"
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 1
        assert SyncOperation.objects.filter(op_id=operation["op_id"]).count() == 2  # 1 rejet + 1 application


def test_sale_to_customer_not_created_is_dependency_missing(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 5)
    customer_id = str(uuid.uuid4())
    sale = sale_op(tenant_a, p, 1, customer=customer_id)
    bad_customer = op("customer.create", {"name": "Sans id"})  # invalide : id manquant
    res = results(push(tenant_a.client(), [bad_customer, sale]))
    assert res[0]["status"] == "rejected"
    assert res[1]["status"] == "rejected" and res[1]["error"]["code"] == "dependency_missing"


# --------------------------------------------------------------------------- stock négatif


def test_insufficient_stock_sale_is_accepted_with_warning(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 1)
    r = results(push(tenant_a.client(), [sale_op(tenant_a, p, 3)]))[0]
    assert r["status"] == "applied"
    warning = next(w for w in r["warnings"] if w["code"] == "negative_stock")
    assert warning["details"]["product"] == str(p.id)
    assert Decimal(warning["details"]["quantity"]) == Decimal("-2")
    with tenant_context(tenant_a.tenant.id):
        assert get_quantity(p, tenant_a.store) == -2
        assert Sale.objects.count() == 1


def test_no_warning_when_stock_sufficient_and_service_untracked(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    svc = make_product(tenant_a, price_minor=1000, sku="S1", kind="service")
    stock(tenant_a, p, 5)
    res = results(push(tenant_a.client(), [sale_op(tenant_a, p), sale_op(tenant_a, svc)]))
    assert all("warnings" not in r for r in res)


def test_stock_movement_op_and_negative_warning(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 2)
    base = {"product": str(p.id), "store": str(tenant_a.store.id)}
    res = results(push(tenant_a.client(), [
        op("stock.movement", {**base, "kind": "in", "quantity": "5", "unit_cost_minor": 800}),
        op("stock.movement", {**base, "kind": "out", "quantity": "10", "reason": "Casse constatée"}),
        op("stock.movement", {**base, "kind": "out", "quantity": "1"}),  # motif manquant
    ]))
    assert [r["status"] for r in res] == ["applied", "applied", "rejected"]
    assert res[1]["warnings"][0]["code"] == "negative_stock"
    with tenant_context(tenant_a.tenant.id):
        assert get_quantity(p, tenant_a.store) == -3
        assert AuditEntry.objects.filter(action="stock.movement").count() == 2


# --------------------------------------------------------------------------- horloge


def test_clock_skew_warning_and_server_time_is_used(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 5)
    far_past = timezone.now() - timedelta(hours=3)
    future = timezone.now() + timedelta(hours=1)
    near = minutes_ago(5)
    res = results(push(tenant_a.client(), [
        sale_op(tenant_a, p, device_time=far_past),
        sale_op(tenant_a, p, device_time=future),
        sale_op(tenant_a, p, device_time=near),
    ]))
    assert res[0]["warnings"][0]["code"] == "clock_skew"
    assert res[1]["warnings"][0]["code"] == "clock_skew"
    assert "warnings" not in res[2]
    with tenant_context(tenant_a.tenant.id):
        sale = Sale.objects.get(pk=res[0]["entity_id"])
        assert abs(sale.created_at - timezone.now()) < timedelta(minutes=1)  # heure serveur
        assert abs(sale.device_time - far_past) < timedelta(seconds=1)  # heure appareil conservée
        op_row = SyncOperation.objects.get(op_id=res[0]["op_id"])
        assert abs(op_row.device_time - far_past) < timedelta(seconds=1)
        assert abs(op_row.received_at - timezone.now()) < timedelta(minutes=1)


# --------------------------------------------------------------------------- clients


def test_customer_create_update_and_duplicate_id(tenant_a):
    c = tenant_a.client()
    cid = str(uuid.uuid4())
    r = results(push(c, [op("customer.create", {"id": cid, "name": "Awa", "phone": "+243811111111"})]))[0]
    assert r["status"] == "applied" and r["entity_id"] == cid
    again = results(push(c, [op("customer.create", {"id": cid, "name": "Autre nom"})]))[0]
    assert again["status"] == "duplicate" and again["entity_id"] == cid
    r = results(push(c, [op("customer.update", {"id": cid, "name": "Awa K.", "notes": "fidèle"})]))[0]
    assert r["status"] == "applied"
    with tenant_context(tenant_a.tenant.id):
        customer = Customer.objects.get(pk=cid)
        assert (customer.name, customer.notes, customer.phone) == ("Awa K.", "fidèle", "+243811111111")
        assert Customer.objects.count() == 1
        assert AuditEntry.objects.filter(action__in=["customer.create", "customer.update"]).count() == 2


def test_customer_update_ignores_sensitive_fields(tenant_a):
    cust = make_customer(tenant_a, "Bob", limit=50_000, ccy="CDF")
    r = results(push(tenant_a.client(), [op("customer.update", {
        "id": str(cust.id), "name": "Bob 2", "credit_limit_minor": 9_999_999,
        "credit_limit_currency": "USD"}, device_time=timezone.now())]))[0]
    assert r["status"] == "applied"
    warning = next(w for w in r["warnings"] if w["code"] == "sensitive_field_ignored")
    assert set(warning["details"]["fields"]) == {"credit_limit_minor", "credit_limit_currency"}
    with tenant_context(tenant_a.tenant.id):
        cust.refresh_from_db()
        assert cust.name == "Bob 2"
        assert (cust.credit_limit_minor, cust.credit_limit_currency) == (50_000, "CDF")


def test_customer_create_ignores_credit_limit(tenant_a):
    cid = str(uuid.uuid4())
    r = results(push(tenant_a.client(), [
        op("customer.create", {"id": cid, "name": "Neo", "credit_limit_minor": 1_000_000})]))[0]
    assert r["warnings"][0]["code"] == "sensitive_field_ignored"
    with tenant_context(tenant_a.tenant.id):
        assert Customer.objects.get(pk=cid).credit_limit_minor == 0


def test_customer_update_last_write_wins(tenant_a):
    c = tenant_a.client()
    cust = make_customer(tenant_a, "Original")
    t0 = timezone.now()
    # Deux mises à jour du même appareil dans l'ordre : les deux s'appliquent, la dernière gagne.
    res = results(push(c, [
        op("customer.update", {"id": str(cust.id), "name": "V1"}, device_time=t0 + timedelta(seconds=1)),
        op("customer.update", {"id": str(cust.id), "name": "V2"}, device_time=t0 + timedelta(seconds=2)),
    ]))
    assert [r["status"] for r in res] == ["applied", "applied"]
    # Un autre appareil, avec une heure antérieure à V2 : perdante (stale), la fiche reste V2.
    old = results(push(c, [op("customer.update", {"id": str(cust.id), "name": "Ancien"},
                             device_time=t0 - timedelta(seconds=30))]))[0]
    assert old["status"] == "rejected" and old["error"]["code"] == "stale_update"
    with tenant_context(tenant_a.tenant.id):
        assert Customer.objects.get(pk=cust.pk).name == "V2"


def test_customer_update_loses_against_later_web_edit(tenant_a):
    cust = make_customer(tenant_a, "Original")
    edit_time = timezone.now() - timedelta(minutes=2)
    with tenant_context(tenant_a.tenant.id):
        cust.name = "Modifié en ligne"
        cust.save()
    r = results(push(tenant_a.client(), [op(
        "customer.update", {"id": str(cust.id), "name": "Hors ligne"}, device_time=edit_time)]))[0]
    assert r["error"]["code"] == "stale_update"
    later = results(push(tenant_a.client(), [op(
        "customer.update", {"id": str(cust.id), "name": "Hors ligne 2"}, device_time=timezone.now())]))[0]
    assert later["status"] == "applied"


def test_customer_update_unknown_is_dependency_missing(tenant_a):
    r = results(push(tenant_a.client(), [op("customer.update", {"id": str(uuid.uuid4()), "name": "x"})]))[0]
    assert r["error"]["code"] == "dependency_missing"


# --------------------------------------------------------------------------- caisse et annulation


def test_cashier_offline_flow_open_sales_close(tenant_a):
    cashier = client_for_role(tenant_a, "cashier")
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 10)
    store = str(tenant_a.store.id)
    open_op = op("cash_session.open", {"store": store, "opening_float": {"CDF": 5000}})
    res = results(push(cashier, [
        open_op,
        sale_op(tenant_a, p, 2),
        sale_op(tenant_a, p, 1),
        op("cash_session.close", {"open_op_id": open_op["op_id"], "counted": {"CDF": 8000, "USD": 0}}),
    ]))
    assert [r["status"] for r in res] == ["applied"] * 4, res
    with tenant_context(tenant_a.tenant.id):
        session = CashSession.objects.get()
        assert str(session.id) == res[0]["entity_id"] == res[3]["entity_id"]
        assert session.status == "closed"
        assert session.expected["CDF"] == 8000 and session.difference["CDF"] == 0
        assert Sale.objects.filter(cash_session=session).count() == 2


def test_cashier_sale_without_open_session_is_dependency_missing(tenant_a):
    cashier = client_for_role(tenant_a, "cashier")
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 10)
    r = results(push(cashier, [sale_op(tenant_a, p)]))[0]
    assert r["status"] == "rejected" and r["error"]["code"] == "dependency_missing"


def test_cash_session_open_twice_conflicts_and_close_by_store(tenant_a):
    c = tenant_a.client()
    store = str(tenant_a.store.id)
    res = results(push(c, [
        op("cash_session.open", {"store": store}),
        op("cash_session.open", {"store": store}),
        op("cash_session.close", {"store": store, "counted": {"CDF": 0, "USD": 0}}),
        op("cash_session.close", {"store": store, "counted": {"CDF": 0, "USD": 0}}),
    ]))
    assert [r["status"] for r in res] == ["applied", "rejected", "applied", "rejected"]
    assert res[1]["error"]["code"] == "conflict"
    assert res[3]["error"]["code"] == "dependency_missing"


def test_sale_cancel_by_client_id_and_credit_sale(tenant_a):
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 10)
    cust = make_customer(tenant_a, "Crédit", limit=100_000)
    sale = sale_op(tenant_a, p, 3, customer=str(cust.id), payments=[], credit_remaining=True)
    cancel = op("sale.cancel", {"client_id": sale["payload"]["client_id"], "reason": "Erreur de saisie"})
    res = results(push(tenant_a.client(), [sale, cancel]))
    assert [r["status"] for r in res] == ["applied", "applied"]
    assert res[1]["entity_id"] == res[0]["entity_id"]
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.get().status == "cancelled"
        assert get_quantity(p, tenant_a.store) == 10
    replay = results(push(tenant_a.client(), [cancel]))[0]
    assert replay["status"] == "duplicate"
    other = op("sale.cancel", {"sale_id": res[0]["entity_id"], "reason": "Encore"})
    assert results(push(tenant_a.client(), [other]))[0]["error"]["code"] == "conflict"


def test_cancel_of_unknown_sale_is_dependency_missing(tenant_a):
    r = results(push(tenant_a.client(), [op("sale.cancel", {"client_id": str(uuid.uuid4()), "reason": "x"})]))[0]
    assert r["error"]["code"] == "dependency_missing"
    r = results(push(tenant_a.client(), [op("sale.cancel", {"sale_id": "nope", "reason": "x"})]))[0]
    assert r["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------- enveloppe et limites


def test_batch_limit_is_200(tenant_a):
    c = tenant_a.client()
    ok = [op("customer.create", {"id": str(uuid.uuid4()), "name": f"C{i}"}) for i in range(200)]
    res = results(push(c, ok))
    assert len(res) == 200 and all(r["status"] == "applied" for r in res)
    too_many = [op("customer.create", {"id": str(uuid.uuid4()), "name": f"D{i}"}) for i in range(201)]
    response = push(c, too_many)
    assert response.status_code == 400
    assert response.json()["code"] == "batch_too_large"
    with tenant_context(tenant_a.tenant.id):
        assert Customer.objects.count() == 200  # le lot trop gros n'a rien appliqué


@pytest.mark.parametrize("bad", [
    {},
    {"device_id": "x", "operations": []},
    {"device_id": DEVICE, "operations": []},
    {"device_id": DEVICE, "operations": [{"op_id": "x", "type": "sale.create", "payload": {}}]},
    {"device_id": DEVICE, "operations": [
        {"op_id": str(uuid.uuid4()), "type": "sale.create", "device_time": "hier", "payload": {}}]},
    {"device_id": DEVICE, "operations": [
        {"op_id": str(uuid.uuid4()), "type": "sale.create", "device_time": "2026-01-01T00:00:00Z"}]},
])
def test_malformed_envelope_is_400(tenant_a, bad):
    assert tenant_a.client().post(PUSH, bad, format="json").status_code == 400


def test_journal_is_append_only(tenant_a):
    results(push(tenant_a.client(), [op("customer.create", {"id": str(uuid.uuid4()), "name": "J"})]))
    with tenant_context(tenant_a.tenant.id):
        row = SyncOperation.objects.get()
        assert row.device_id == uuid.UUID(DEVICE)
        row.status = "rejected"
        with pytest.raises(AppendOnlyError):
            row.save()
        with pytest.raises(AppendOnlyError):
            row.delete()
        with pytest.raises(AppendOnlyError):
            SyncOperation.objects.all().update(status="rejected")
        with pytest.raises(AppendOnlyError):
            SyncOperation.objects.all().delete()


# --------------------------------------------------------------------------- permissions


@pytest.mark.parametrize("role,allowed", [
    ("owner", True), ("manager", True), ("cashier", True), ("storekeeper", False), ("accountant", False),
])
def test_push_permissions_by_role(tenant_a, role, allowed):
    client = client_for_role(tenant_a, role)
    response = push(client, [op("customer.create", {"id": str(uuid.uuid4()), "name": "Perm"})])
    assert response.status_code == (200 if allowed else 403)
    if allowed:
        with tenant_context(tenant_a.tenant.id):
            assert Customer.objects.count() == 1


def test_push_requires_authentication(tenant_a):
    from rest_framework.test import APIClient

    assert APIClient().post(PUSH, {}, format="json").status_code == 401


def test_cashier_cannot_cancel_or_move_stock(tenant_a):
    cashier = client_for_role(tenant_a, "cashier")
    manager = client_for_role(tenant_a, "manager")
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 5)
    sale = sale_op(tenant_a, p)
    results(push(manager, [sale]))
    res = results(push(cashier, [
        op("sale.cancel", {"client_id": sale["payload"]["client_id"], "reason": "non"}),
        op("stock.movement", {"product": str(p.id), "store": str(tenant_a.store.id), "kind": "in",
                              "quantity": "5"}),
    ]))
    assert [r["error"]["code"] for r in res] == ["forbidden", "forbidden"]
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.get().status == "completed"
        assert get_quantity(p, tenant_a.store) == 4


def test_cashier_cannot_set_unauthorized_price(tenant_a):
    cashier = client_for_role(tenant_a, "cashier")
    p = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, p, 5)
    results(push(cashier, [op("cash_session.open", {"store": str(tenant_a.store.id)})]))
    cheat = sale_op(tenant_a, p)
    cheat["payload"]["items"][0]["unit_price_minor"] = 1
    r = results(push(cashier, [cheat]))[0]
    assert r["status"] == "rejected" and r["error"]["code"] == "forbidden"


# --------------------------------------------------------------------------- isolation


def test_tenants_may_reuse_the_same_op_id(tenant_a, tenant_b):
    shared = uuid.uuid4()
    pa = make_product(tenant_a, price_minor=1000, sku="P1")
    pb = make_product(tenant_b, price_minor=1000, sku="P1")
    stock(tenant_a, pa, 5)
    stock(tenant_b, pb, 5)
    ra = results(push(tenant_a.client(), [sale_op(tenant_a, pa, op_id=shared)]))[0]
    rb = results(push(tenant_b.client(), [sale_op(tenant_b, pb, op_id=shared)]))[0]
    assert ra["status"] == "applied" and rb["status"] == "applied"
    assert ra["entity_id"] != rb["entity_id"]
    with tenant_context(tenant_a.tenant.id):
        assert SyncOperation.objects.count() == 1 and Sale.objects.count() == 1
    with tenant_context(tenant_b.tenant.id):
        assert SyncOperation.objects.count() == 1 and Sale.objects.count() == 1


def test_tenant_b_cannot_read_result_of_tenant_a_op_id(tenant_a, tenant_b):
    pa = make_product(tenant_a, price_minor=1000, sku="P1")
    stock(tenant_a, pa, 5)
    secret = sale_op(tenant_a, pa)
    ra = results(push(tenant_a.client(), [secret]))[0]
    # B rejoue l'op_id de A avec un contenu anodin : il n'obtient ni le résultat ni l'entity_id de A.
    probe = op("customer.create", {"id": str(uuid.uuid4()), "name": "Sonde"}, op_id=secret["op_id"])
    rb = results(push(tenant_b.client(), [probe]))[0]
    assert rb["status"] == "applied"
    assert rb["entity_id"] != ra["entity_id"]
    assert "data" not in rb
    # B ne peut pas annuler ni référencer la vente de A.
    cancel = op("sale.cancel", {"sale_id": ra["entity_id"], "reason": "intrusion"})
    rc = results(push(tenant_b.client(), [cancel]))[0]
    assert rc["status"] == "rejected" and rc["error"]["code"] == "dependency_missing"
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.get().status == "completed"


def test_customer_id_of_other_tenant_is_not_disclosed(tenant_a, tenant_b):
    foreign = make_customer(tenant_a, "Chez A")
    r = results(push(tenant_b.client(), [op("customer.create", {"id": str(foreign.id), "name": "Vol"})]))[0]
    assert r["status"] == "rejected" and r["error"]["code"] == "conflict"
    r = results(push(tenant_b.client(), [op("customer.update", {"id": str(foreign.id), "name": "Vol"})]))[0]
    assert r["error"]["code"] == "dependency_missing"
    with tenant_context(tenant_a.tenant.id):
        assert Customer.objects.get(pk=foreign.pk).name == "Chez A"
