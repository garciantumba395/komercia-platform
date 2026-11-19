"""API de sauvegarde locale. Réservée à l'édition locale (`KOMERSIA_EDITION=local`) : 404 sinon."""

# ruff: noqa: E501

from django.conf import settings
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.services import log
from core.permissions import MANAGER, OWNER, HasRole

from . import services
from .models import BackupRecord, BackupSettings


class LocalEditionOnly:
    """Cette fonction n'existe que sur le PC principal : 404 partout ailleurs (avant toute autre vérification)."""

    def initial(self, request, *args, **kwargs):
        if getattr(settings, "KOMERSIA_EDITION", "cloud") != "local":
            raise Http404
        return super().initial(request, *args, **kwargs)


class RecordSerializer(serializers.ModelSerializer):
    filename = serializers.CharField(read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = BackupRecord
        fields = ["id", "filename", "path", "size_bytes", "sha256", "status", "reason", "message", "attempts",
                  "created_at", "updated_at", "created_by_name"]

    def get_created_by_name(self, obj) -> str:
        return obj.created_by.full_name if obj.created_by_id else ""


class SettingsSerializer(serializers.Serializer):
    folder = serializers.CharField(allow_blank=True)
    retention = serializers.IntegerField()
    preferred_hour = serializers.IntegerField()
    interval_hours = serializers.IntegerField()
    enabled = serializers.BooleanField()


class SettingsInput(serializers.Serializer):
    folder = serializers.CharField(required=False, max_length=500)
    retention = serializers.IntegerField(required=False, min_value=services.RETENTION_RANGE[0],
                                         max_value=services.RETENTION_RANGE[1])
    preferred_hour = serializers.IntegerField(required=False, min_value=0, max_value=23)
    interval_hours = serializers.IntegerField(required=False, min_value=services.INTERVAL_RANGE[0],
                                              max_value=services.INTERVAL_RANGE[1])
    enabled = serializers.BooleanField(required=False)

    def validate_folder(self, value):
        try:
            return services.validate_folder(value)
        except services.FolderError as exc:
            raise serializers.ValidationError(str(exc)) from None


def settings_payload(cfg: BackupSettings) -> dict:
    return {"folder": cfg.folder, "retention": cfg.retention, "preferred_hour": cfg.preferred_hour,
            "interval_hours": cfg.interval_hours, "enabled": cfg.enabled}


class StatusView(LocalEditionOnly, APIView):
    """État de la protection : dernière sauvegarde réussie, âge, prochain passage, dossier, espace libre."""

    permission_classes = [HasRole]
    allowed_roles = [OWNER, MANAGER]

    @extend_schema(responses={200: dict})
    def get(self, request):
        now = timezone.now()
        cfg = BackupSettings.get_solo()
        last = services.last_success()
        latest = BackupRecord.objects.first()
        state, message = services.folder_state(cfg.folder)
        free = services.free_bytes(cfg.folder) if state == "ok" else None
        data = {
            "enabled": cfg.enabled,
            "folder": cfg.folder,
            "folder_state": state,
            "folder_message": message,
            "free_bytes": free,
            "retention": cfg.retention,
            "preferred_hour": cfg.preferred_hour,
            "interval_hours": cfg.interval_hours,
            "installed_at": cfg.created_at,
            "last_success": RecordSerializer(last).data if last else None,
            "last_attempt": RecordSerializer(latest).data if latest else None,
            "next_run_at": services.due_at(cfg, last, now) if cfg.enabled else None,
            "backup_count": len(services.list_backup_files(cfg.folder)) if state == "ok" else None,
            "server_time": now,
        }
        data.update(services.assess(cfg, last, latest, now))
        return Response(data)


class RecordListView(LocalEditionOnly, ListAPIView):
    """Historique des sauvegardes (paginé, le plus récent d'abord)."""

    permission_classes = [HasRole]
    allowed_roles = [OWNER, MANAGER]
    serializer_class = RecordSerializer
    queryset = BackupRecord.objects.select_related("created_by")


class RunView(LocalEditionOnly, APIView):
    """Sauvegarde immédiate ; la réponse (synchrone) donne le résultat."""

    permission_classes = [HasRole]
    allowed_roles = [OWNER, MANAGER]

    @extend_schema(request=None, responses={201: RecordSerializer, 409: dict})
    def post(self, request):
        try:
            record = services.create_backup("manual", user=request.user)
        except services.BackupBusy as exc:
            return Response({"detail": str(exc), "code": "backup_busy"}, status=status.HTTP_409_CONFLICT)
        log("backup.run", "backup", record.id, user=request.user, request=request, after={
            "status": record.status, "filename": record.filename, "size_bytes": record.size_bytes,
            "message": record.message})
        body = RecordSerializer(record).data
        return Response({"ok": record.status == "ok", "record": body, "message": record.message},
                        status=status.HTTP_201_CREATED)


class SettingsView(LocalEditionOnly, APIView):
    """Réglages : lecture pour propriétaire et gérant, écriture pour le propriétaire seul."""

    permission_classes = [HasRole]
    allowed_roles = [OWNER, MANAGER]

    def initial(self, request, *args, **kwargs):
        self.allowed_roles = [OWNER] if request.method == "PUT" else [OWNER, MANAGER]
        return super().initial(request, *args, **kwargs)

    @extend_schema(responses=SettingsSerializer)
    def get(self, request):
        return Response(settings_payload(BackupSettings.get_solo()))

    @extend_schema(request=SettingsInput, responses=SettingsSerializer)
    def put(self, request):
        cfg = BackupSettings.get_solo()
        before = settings_payload(cfg)
        data = SettingsInput(data=request.data)
        data.is_valid(raise_exception=True)
        for key, value in data.validated_data.items():
            setattr(cfg, key, value)
        cfg.save()
        after = settings_payload(cfg)
        if after != before:
            log("backup.settings_change", "backup_settings", 1, user=request.user, request=request,
                before=before, after=after)
        return Response(after)
