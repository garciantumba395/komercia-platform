"""Mixin DRF pour journaliser les écritures d'un ViewSet."""

from . import services


class AuditedMixin:
    """Journalise create / update / destroy d'un ModelViewSet avec before/after.

    Usage (dans un autre module) :

        class ProductViewSet(AuditedMixin, viewsets.ModelViewSet):
            audit_entity_type = "product"      # défaut : nom du modèle en minuscules
            audit_prefix = "product"           # défaut : audit_entity_type
            audit_fields = ["name", "price"]   # défaut : tous les champs du modèle
            audit_actions = {"update": "price.change"}  # surcharge optionnelle du code d'action

    Codes d'action par défaut : "<prefix>.create", "<prefix>.update", "<prefix>.delete".
    Le motif éventuel est lu dans la donnée `reason` de la requête (hors serializer) et
    `device_time` dans `device_time` (ISO 8601) pour les opérations hors ligne.
    L'entrée d'audit est écrite dans la même transaction que la modification.
    Pour des actions métier (annulation...), appeler `audit.services.log` directement.
    """

    audit_entity_type = None
    audit_prefix = None
    audit_fields = None
    audit_actions: dict = {}

    def _audit_type(self, instance):
        return self.audit_entity_type or instance._meta.model_name

    def _audit_action(self, kind, instance):
        default = f"{self.audit_prefix or self._audit_type(instance)}.{kind}"
        return self.audit_actions.get(kind, default)

    def _audit_extra(self):
        from django.utils.dateparse import parse_datetime

        data = self.request.data if hasattr(self.request.data, "get") else {}
        raw = data.get("device_time")
        device_time = parse_datetime(raw) if isinstance(raw, str) else None
        reason = data.get("reason", "")
        return {"reason": reason if isinstance(reason, str) else "", "device_time": device_time}

    def _audit(self, kind, instance, before, after, pk=None):
        services.log(
            self._audit_action(kind, instance),
            self._audit_type(instance),
            instance.pk if pk is None else pk,
            before=before,
            after=after,
            request=self.request,
            **self._audit_extra(),
        )

    def perform_create(self, serializer):
        super().perform_create(serializer)
        instance = serializer.instance
        self._audit("create", instance, None, services.snapshot(instance, self.audit_fields))

    def perform_update(self, serializer):
        before = services.snapshot(serializer.instance, self.audit_fields)
        super().perform_update(serializer)
        instance = serializer.instance
        self._audit("update", instance, before, services.snapshot(instance, self.audit_fields))

    def perform_destroy(self, instance):
        before = services.snapshot(instance, self.audit_fields)
        pk = instance.pk  # Django remet instance.pk à None après suppression
        super().perform_destroy(instance)
        self._audit("delete", instance, before, None, pk=pk)
