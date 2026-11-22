from django.contrib import admin

from .models import BillingNotice, Plan, Subscription, SubscriptionPayment


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price_usd_minor", "extra_store_price_usd_minor", "max_stores",
                    "max_users", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")


class _ReadOnly(admin.ModelAdmin):
    """Lecture seule. Ces tables sont isolées par RLS : l'admin n'y voit que les lignes du tenant
    actif dans la session (voir INTEGRATION.md)."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(_ReadOnly):
    list_display = ("reference", "tenant", "plan", "amount_minor", "currency", "status", "created_at")
    list_filter = ("status", "currency")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "plan", "state", "trial_ends_at", "current_period_end")
    list_filter = ("state",)


@admin.register(BillingNotice)
class BillingNoticeAdmin(_ReadOnly):
    list_display = ("subscription", "kind", "due_at", "read_at")
