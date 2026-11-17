import uuid

from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from core.permissions import ACCOUNTANT, MANAGER, OWNER, HasRole

from .models import AuditEntry
from .serializers import AuditEntrySerializer

PARAMS = [
    OpenApiParameter(n, str, description=d)
    for n, d in [
        ("action", "Code d'action exact (ex. sale.cancel)"),
        ("entity_type", "Type d'entité"),
        ("entity_id", "Identifiant de l'entité"),
        ("user", "Identifiant de l'utilisateur"),
        ("date_from", "Date de début incluse (AAAA-MM-JJ)"),
        ("date_to", "Date de fin incluse (AAAA-MM-JJ)"),
    ]
]


@extend_schema(parameters=PARAMS)
class AuditEntryViewSet(viewsets.ReadOnlyModelViewSet):
    """Journal d'audit en lecture seule (propriétaire, gérant, comptable)."""

    serializer_class = AuditEntrySerializer
    permission_classes = [HasRole]
    allowed_roles = [OWNER, MANAGER, ACCOUNTANT]

    def get_queryset(self):
        qs = AuditEntry.objects.select_related("user")  # filtré par la RLS
        p = self.request.query_params
        for param in ("action", "entity_type", "entity_id"):
            if p.get(param):
                qs = qs.filter(**{param: p[param]})
        if p.get("user"):
            try:
                user_id = uuid.UUID(p["user"])
            except ValueError:
                raise ValidationError({"user": "Identifiant invalide."}) from None
            qs = qs.filter(user_id=user_id)
        for param, lookup in (("date_from", "created_at__date__gte"),
                              ("date_to", "created_at__date__lte")):
            if p.get(param):
                d = parse_date(p[param])
                if d is None:
                    raise ValidationError({param: "Date invalide (AAAA-MM-JJ)."})
                qs = qs.filter(**{lookup: d})
        return qs
