from collections import defaultdict
from datetime import date

from django.db import transaction
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import ACCOUNTANT, CASHIER, MANAGER, OWNER, HasRole

from . import services
from .models import CreditEntry, Customer
from .serializers import CreditEntrySerializer, CustomerSerializer, PaymentInputSerializer

READ_ROLES = [OWNER, MANAGER, CASHIER, ACCOUNTANT]
WRITE_ROLES = [OWNER, MANAGER, CASHIER]
LIMIT_ROLES = [OWNER, MANAGER]
LIMIT_FIELDS = ("credit_limit_minor", "credit_limit_currency")


class CustomerViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerSerializer
    permission_classes = [HasRole]
    allowed_roles_by_action = {
        "list": READ_ROLES, "retrieve": READ_ROLES, "statement": READ_ROLES,
        "create": WRITE_ROLES, "update": WRITE_ROLES, "partial_update": WRITE_ROLES,
        "destroy": WRITE_ROLES, "payments": WRITE_ROLES,
    }

    def get_queryset(self):  # filtré par la RLS
        qs = Customer.objects.order_by("name")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))
        if self.request.query_params.get("with_debt") in ("1", "true"):
            ids = {cid for cid, b in services.balances_by_customer().items()
                   if any(v > 0 for v in b.values())}
            qs = qs.filter(id__in=ids)
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if getattr(self, "action", None) == "list":
            ctx["balances"] = services.balances_by_customer()
        return ctx

    def _guard_limit(self, request, creating=False):
        touched = [f for f in LIMIT_FIELDS if f in request.data]
        if touched and request.membership.role not in LIMIT_ROLES:
            raise PermissionDenied("Seuls le propriétaire et le gérant peuvent modifier la limite de crédit.")

    def create(self, request, *args, **kwargs):
        self._guard_limit(request, creating=True)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self._guard_limit(request)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        customer = self.get_object()
        if customer.credit_entries.exists():
            customer.is_active = False
            customer.save(update_fields=["is_active", "updated_at"])
            return Response({"detail": "Client avec historique de crédit : désactivé, non supprimé."},
                            status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["get"])
    def statement(self, request, pk=None):
        customer = self.get_object()
        entries = (CreditEntry.objects.filter(customer=customer)
                   .prefetch_related("allocations_made", "allocations_received")
                   .order_by("created_at", "id"))
        return Response({
            "customer": {"id": str(customer.id), "name": customer.name},
            "balance": services.get_balance(customer),
            "entries": CreditEntrySerializer(entries, many=True).data,
        })

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def payments(self, request, pk=None):
        customer = self.get_object()
        s = PaymentInputSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            entry = services.register_payment(customer, user=request.user, **s.validated_data)
        except services.CreditError as e:
            return Response({"detail": e.message}, status=status.HTTP_400_BAD_REQUEST)
        entry = CreditEntry.objects.prefetch_related("allocations_made").get(pk=entry.pk)
        return Response({"entry": CreditEntrySerializer(entry).data,
                         "balance": services.get_balance(customer)}, status=status.HTTP_201_CREATED)


def _debts(today=None):
    """Clients endettés, du plus ancien impayé au plus récent, avec indicateur de retard."""
    today = today or date.today()
    per = defaultdict(list)
    for c in services.open_charges():
        per[c.customer_id].append(c)
    customers = Customer.objects.in_bulk(per.keys())
    rows = []
    for cid, charges in per.items():
        balance: dict[str, int] = defaultdict(int)
        overdue: dict[str, int] = defaultdict(int)
        for c in charges:
            balance[c.currency] += c.open_minor
            if services.is_overdue(c.due_date, today):
                overdue[c.currency] += c.open_minor
        oldest = min(charges, key=lambda c: (c.created_at, c.id))
        due_dates = [c.due_date for c in charges if c.due_date]
        rows.append({
            "customer": {"id": str(cid), "name": customers[cid].name, "phone": customers[cid].phone},
            "balance": dict(balance),
            "oldest_debt_at": oldest.created_at,
            "next_due_date": min(due_dates) if due_dates else None,
            "is_overdue": bool(overdue),
            "overdue_balance": dict(overdue),
            "days_overdue": max((today - d).days for d in due_dates if d < today) if overdue else 0,
        })
    rows.sort(key=lambda r: r["oldest_debt_at"])
    return rows


class DebtListView(APIView):
    permission_classes = [HasRole]
    allowed_roles = READ_ROLES

    def get(self, request):
        return Response(_debts())


class OverdueDebtListView(APIView):
    permission_classes = [HasRole]
    allowed_roles = READ_ROLES

    def get(self, request):
        return Response([r for r in _debts() if r["is_overdue"]])
