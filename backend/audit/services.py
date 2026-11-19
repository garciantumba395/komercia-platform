"""Services du journal d'audit, appelés par les autres modules."""

import datetime
import decimal
import enum
import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models as dj_models

from .models import AuditEntry


def _jsonable(value):
    """Convertit récursivement une valeur en équivalent JSON (Decimal -> str, UUID -> str,
    dates -> ISO 8601, modèle -> sa clé primaire)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return DjangoJSONEncoder().default(value)
    if isinstance(value, datetime.timedelta):
        return DjangoJSONEncoder().default(value)
    if isinstance(value, enum.Enum):
        return _jsonable(value.value)
    if isinstance(value, dj_models.Model):
        return _jsonable(value.pk)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


def snapshot(instance, fields=None) -> dict:
    """État JSON-sérialisable d'un modèle, utilisable pour `before` / `after`.

    `fields` : liste de noms de champs à retenir (par défaut tous les champs concrets, hors
    `password`). Les clés étrangères sont exprimées par `<champ>_id`.
    """
    data = {}
    for field in instance._meta.concrete_fields:
        if fields is not None and field.name not in fields and field.attname not in fields:
            continue
        if fields is None and field.name == "password":
            continue
        data[field.attname] = _jsonable(getattr(instance, field.attname))
    return data


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def log(
    action,
    entity_type,
    entity_id,
    *,
    before=None,
    after=None,
    user=None,
    reason="",
    device_time=None,
    request=None,
) -> AuditEntry:
    """Enregistre une entrée d'audit pour le tenant actif.

    Si `request` est fourni, l'utilisateur (request.user) et l'adresse IP en sont tirés.
    """
    ip = None
    if request is not None:
        request_user = getattr(request, "user", None)
        if user is None and request_user is not None and request_user.is_authenticated:
            user = request_user
        ip = _client_ip(request)
        try:
            from django.core.validators import validate_ipv46_address

            validate_ipv46_address(ip)
        except Exception:
            ip = None
    return AuditEntry.objects.create(
        user=user,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before=None if before is None else _jsonable(before),
        after=None if after is None else _jsonable(after),
        reason=reason or "",
        device_time=device_time,
        ip_address=ip,
    )
