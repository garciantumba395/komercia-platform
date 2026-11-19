"""Lectures de la console, TOUTES sur l'alias `ops` (BYPASSRLS, lecture seule).

Deux familles :
- informations d'exploitation (liste, fiche : abonnement, compte, compteurs) : toujours disponibles ;
- données détaillées d'une entreprise (ventes, produits, clients) : `detail_*`, à n'appeler qu'après avoir
  vérifié un consentement valide ET journalisé la consultation (voir `views.support_data`).

Jamais de `select_related("user")` ni de chargement d'un `User` complet : le rôle `ops` ne peut pas lire la
colonne `password`. On utilise `values()` sur des colonnes autorisées.
"""

from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models import Membership, Store, Tenant
from audit.models import AuditEntry
from billing.models import Subscription, SubscriptionPayment
from catalog.models import Product
from core.permissions import OWNER
from customers.models import Customer
from sales.models import Sale

from .metrics import month_start
from .models import FeatureFlag, SupportAccessGrant

OPS = "ops"
PAGE_SIZE = 25


def tenant_list_queryset(search="", state="", status=""):
    sub = Subscription.objects.using(OPS).filter(tenant_id=OuterRef("pk"))
    owner = (
        Membership.objects.using(OPS)
        .filter(tenant_id=OuterRef("pk"), role=OWNER)
        .order_by("created_at")
    )
    stores = (
        Store.objects.using(OPS)
        .filter(tenant_id=OuterRef("pk"), is_active=True)
        .order_by()
        .values("tenant_id")
        .annotate(c=Count("id"))
        .values("c")
    )
    members = (
        Membership.objects.using(OPS)
        .filter(tenant_id=OuterRef("pk"), is_active=True)
        .order_by()
        .values("tenant_id")
        .annotate(c=Count("id"))
        .values("c")
    )
    qs = Tenant.objects.using(OPS).annotate(
        sub_state=Subquery(sub.values("state")[:1]),
        plan_name=Subquery(sub.values("plan__name")[:1]),
        trial_ends_at=Subquery(sub.values("trial_ends_at")[:1]),
        period_end=Subquery(sub.values("current_period_end")[:1]),
        last_sale_at=Subquery(
            Sale.objects.using(OPS).filter(tenant_id=OuterRef("pk")).order_by("-created_at").values("created_at")[:1]
        ),
        store_count=Coalesce(Subquery(stores, output_field=IntegerField()), Value(0)),
        user_count=Coalesce(Subquery(members, output_field=IntegerField()), Value(0)),
        owner_phone=Subquery(owner.values("user__phone")[:1]),
        owner_name=Subquery(owner.values("user__full_name")[:1]),
    )
    search = (search or "").strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(owner_phone__icontains=search))
    if state == "none":
        qs = qs.filter(sub_state__isnull=True)
    elif state in dict(Subscription.STATES):
        qs = qs.filter(sub_state=state)
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "suspended":
        qs = qs.filter(is_active=False)
    return qs.order_by("-created_at", "id")


def tenant_overview(tenant_id, now=None):
    """Informations d'exploitation d'une entreprise : jamais de données de vente détaillées."""
    now = now or timezone.now()
    tenant = tenant_list_queryset().filter(pk=tenant_id).first()
    if tenant is None:
        return None
    subscription = (
        Subscription.objects.using(OPS).filter(tenant_id=tenant_id).select_related("plan").first()
    )
    payments = list(
        SubscriptionPayment.objects.using(OPS)
        .filter(tenant_id=tenant_id)
        .order_by("-created_at")
        .values("reference", "plan__name", "kind", "amount_minor", "currency", "amount_usd_minor", "status",
                "provider", "created_at", "confirmed_at")[:10]
    )
    month_sales = Sale.objects.using(OPS).filter(
        tenant_id=tenant_id, status=Sale.COMPLETED, created_at__gte=month_start(now)
    )
    users = list(
        Membership.objects.using(OPS)
        .filter(tenant_id=tenant_id)
        .order_by("created_at")
        .values("role", "is_active", "user__full_name", "user__phone", "user__is_active")
    )
    audit = list(
        AuditEntry.objects.using(OPS)
        .filter(tenant_id=tenant_id)
        .order_by("-created_at", "-id")
        .values("created_at", "action", "entity_type", "reason", "user__full_name")[:15]
    )
    flags = {f["key"]: f["enabled"] for f in FeatureFlag.objects.using(OPS).filter(tenant_id=tenant_id).values("key", "enabled")}
    grants = list(
        SupportAccessGrant.objects.using(OPS)
        .filter(tenant_id=tenant_id)
        .order_by("-created_at")
        .values("created_at", "expires_at", "revoked_at", "scope", "reason", "granted_by__full_name")[:5]
    )
    return {
        "tenant": tenant,
        "subscription": subscription,
        "payments": payments,
        "month_sales_count": month_sales.count(),  # compteur seulement ; les montants exigent un consentement
        "users": users,
        "audit": audit,
        "flags": flags,
        "grants": grants,
    }


# --- Données détaillées (consentement requis, vérifié par l'appelant) -----------------------------------------


def detail_sales(tenant_id, limit=50):
    return list(
        Sale.objects.using(OPS)
        .filter(tenant_id=tenant_id)
        .order_by("-created_at")
        .values("number", "created_at", "status", "currency", "total_minor", "store__name", "cashier__full_name")[:limit]
    )


def detail_products(tenant_id, limit=100):
    return list(
        Product.objects.using(OPS)
        .filter(tenant_id=tenant_id)
        .order_by("name")
        .values("name", "sku", "kind", "price_minor", "currency", "is_active")[:limit]
    )


def detail_customers(tenant_id, limit=100):
    return list(
        Customer.objects.using(OPS)
        .filter(tenant_id=tenant_id)
        .order_by("name")
        .values("name", "phone", "is_active", "created_at")[:limit]
    )


def month_sales_totals(tenant_id, now=None):
    now = now or timezone.now()
    return list(
        Sale.objects.using(OPS)
        .filter(tenant_id=tenant_id, status=Sale.COMPLETED, created_at__gte=month_start(now))
        .values("currency")
        .annotate(total=Sum("total_minor"))
        .order_by("currency")
    )
