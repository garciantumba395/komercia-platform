"""URLConf de test : l'API réelle plus un ViewSet d'exemple utilisant AuditedMixin."""

from django.urls import include, path
from rest_framework import viewsets
from rest_framework.routers import DefaultRouter

from accounts.models import Store
from accounts.serializers import StoreSerializer
from audit.mixins import AuditedMixin
from core.permissions import OWNER, HasRole


class AuditedStoreViewSet(AuditedMixin, viewsets.ModelViewSet):
    serializer_class = StoreSerializer
    permission_classes = [HasRole]
    allowed_roles = [OWNER]
    audit_entity_type = "store"
    audit_fields = ["name", "address", "is_active"]

    def get_queryset(self):
        return Store.objects.order_by("name")


router = DefaultRouter()
router.register("audited-stores", AuditedStoreViewSet, basename="audited-store")

urlpatterns = [
    path("api/v1/audit/", include("audit.urls")),
    path("test/", include(router.urls)),
]
