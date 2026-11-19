"""API du commerçant pour l'accès de support (KOM-082) : `/api/v1/support/`.

Réservée au PROPRIÉTAIRE de l'entreprise. Les routes sont dans `api_urls.py`, à monter dans `komersia/urls.py` :
`path("api/v1/support/", include("backoffice.api_urls"))`.

`SupportAccessGrant` n'étant pas sous RLS, chaque requête filtre EXPLICITEMENT sur `request.tenant`.
Les permissions par défaut sont remplacées : l'accès de support doit rester possible même si l'abonnement est
en lecture seule (c'est précisément le cas où le commerçant peut avoir besoin d'aide) ; il ne modifie aucune
donnée métier.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit import services as audit
from core.permissions import OWNER

from .models import SupportAccessGrant

MIN_HOURS, MAX_HOURS = 1, 72


class IsTenantOwner(BasePermission):
    message = "Seul le propriétaire de l'entreprise peut gérer l'accès de support."

    def has_permission(self, request, view):
        membership = getattr(request, "membership", None)
        return membership is not None and membership.role == OWNER


class GrantSerializer(serializers.Serializer):
    hours = serializers.IntegerField(min_value=MIN_HOURS, max_value=MAX_HOURS)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


def _grant_payload(grant, now):
    return {
        "id": str(grant.id),
        "scope": grant.scope,
        "created_at": grant.created_at,
        "expires_at": grant.expires_at,
        "revoked_at": grant.revoked_at,
        "reason": grant.reason,
        "granted_by": grant.granted_by.full_name,
        "valid": grant.is_valid(now),
    }


def _state(tenant, now):
    grants = list(
        SupportAccessGrant.objects.filter(tenant_id=tenant.id).select_related("granted_by").order_by("-created_at")[:10]
    )
    current = next((g for g in grants if g.is_valid(now)), None)
    return {
        "active": current is not None,
        "current": _grant_payload(current, now) if current else None,
        "history": [_grant_payload(g, now) for g in grants],
        "min_hours": MIN_HOURS,
        "max_hours": MAX_HOURS,
    }


class _SupportBase(APIView):
    permission_classes = [IsAuthenticated, IsTenantOwner]


class SupportStateView(_SupportBase):
    @extend_schema(responses=dict, summary="État de l'accès de support")
    def get(self, request):
        return Response(_state(request.tenant, timezone.now()))


class SupportGrantView(_SupportBase):
    @extend_schema(request=GrantSerializer, responses=dict, summary="Accorder un accès de support temporaire")
    def post(self, request):
        serializer = GrantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        hours, reason = serializer.validated_data["hours"], serializer.validated_data["reason"]
        now = timezone.now()
        with transaction.atomic():
            # Un seul accès valide à la fois : le nouveau remplace l'ancien.
            SupportAccessGrant.objects.filter(
                tenant_id=request.tenant.id, revoked_at__isnull=True, expires_at__gt=now
            ).update(revoked_at=now)
            grant = SupportAccessGrant.objects.create(
                tenant_id=request.tenant.id, granted_by=request.user, expires_at=now + timedelta(hours=hours),
                reason=reason,
            )
            audit.log("support.grant", "tenant", request.tenant.id,
                      after={"expires_at": grant.expires_at, "hours": hours, "scope": grant.scope},
                      reason=reason, request=request)
        return Response(_state(request.tenant, now), status=201)


class SupportRevokeView(_SupportBase):
    @extend_schema(request=None, responses=dict, summary="Révoquer l'accès de support")
    def post(self, request):
        now = timezone.now()
        with transaction.atomic():
            revoked = SupportAccessGrant.objects.filter(
                tenant_id=request.tenant.id, revoked_at__isnull=True, expires_at__gt=now
            ).update(revoked_at=now)
            if revoked:
                audit.log("support.revoke", "tenant", request.tenant.id, after={"revoked": revoked}, request=request)
        return Response(_state(request.tenant, now))
