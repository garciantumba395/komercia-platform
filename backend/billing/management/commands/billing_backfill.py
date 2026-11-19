from django.core.management.base import BaseCommand

from accounts.models import Tenant
from billing import services
from core.tenancy import tenant_context


class Command(BaseCommand):
    help = "Crée un essai de 14 jours (à partir de maintenant) pour les tenants sans abonnement. Idempotent."

    def handle(self, *args, **options):
        created = 0
        for tenant_id in Tenant.objects.values_list("id", flat=True):
            with tenant_context(tenant_id):
                _, was_created = services.ensure_subscription(tenant_id)
            created += was_created
        self.stdout.write(f"{created} abonnement(s) d'essai créé(s).")
