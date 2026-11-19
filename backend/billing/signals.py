from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import Tenant
from core.tenancy import tenant_context

from . import services


@receiver(post_save, sender=Tenant, dispatch_uid="billing_create_trial")
def create_trial_subscription(sender, instance, created, raw=False, **kwargs):
    """Chaque nouveau tenant démarre un essai gratuit de 14 jours (KOM-071)."""
    if not created or raw:
        return
    with tenant_context(instance.id):
        services.ensure_subscription(instance.id)
