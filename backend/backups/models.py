"""Tables de l'application de sauvegarde (édition locale).

Ces deux tables sont SANS Row-Level Security, volontairement (même choix que `backoffice.models`) :

- La sauvegarde concerne l'INSTALLATION entière (toute la base du PC principal), pas un commerçant : il
  n'existe ni tenant ni contexte de tenant pour la planification (`manage.py backup_tick`, lancée par le
  planificateur Windows hors de toute requête). Une politique `tenant_id = app.tenant_id` la rendrait illisible.
- `BackupSettings` est un singleton (clé primaire fixe 1) ; `BackupRecord` est un journal local.
- Aucune donnée secrète : ni mot de passe ni clé. L'accès est restreint par l'API (propriétaire et gérant) ;
  le rôle applicatif y accède par les privilèges par défaut de `scripts/db-init.sql`.
"""

# ruff: noqa: E501

import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

STATUS_OK = "ok"
STATUS_FAILED = "echec"
STATUS_FOLDER_MISSING = "dossier_introuvable"
STATUSES = [
    (STATUS_OK, "Réussie"),
    (STATUS_FAILED, "Échec"),
    (STATUS_FOLDER_MISSING, "Dossier introuvable"),
]

REASON_SCHEDULED = "scheduled"
REASON_MANUAL = "manual"
REASON_PRE_RESTORE = "pre_restore"
REASONS = [
    (REASON_SCHEDULED, "Automatique"),
    (REASON_MANUAL, "Manuelle"),
    (REASON_PRE_RESTORE, "Avant restauration"),
]


class BackupSettings(models.Model):
    """Réglages de la sauvegarde automatique (une seule ligne)."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=True)
    folder = models.CharField(max_length=500, blank=True)
    retention = models.PositiveSmallIntegerField(default=30)
    preferred_hour = models.PositiveSmallIntegerField(default=2)
    interval_hours = models.PositiveSmallIntegerField(default=24)
    # Date de création = date d'installation de la fonction (sert à l'alerte « jamais sauvegardé »).
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    # Dernier contrôle du dossier par la planification (visible dans l'interface).
    folder_state = models.CharField(max_length=30, blank=True)
    folder_checked_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        self.id = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, created = cls.objects.get_or_create(id=1)
        if created and not obj.folder:
            default = os.environ.get("KOMERSIA_BACKUP_DIR", "").strip()
            if default:
                obj.folder = default
                obj.save(update_fields=["folder"])
        return obj


class BackupRecord(models.Model):
    """Une tentative de sauvegarde (réussie ou non)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    path = models.CharField(max_length=1000, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=30, choices=STATUSES)
    reason = models.CharField(max_length=20, choices=REASONS, default=REASON_SCHEDULED)
    message = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"], name="backuprec_status_idx")]

    @property
    def filename(self):
        return os.path.basename(self.path.replace("\\", "/")) if self.path else ""
