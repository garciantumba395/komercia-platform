"""Endpoints de synchronisation pour l'application Android hors ligne (`/api/v1/sync/`).

Protocole complet : `docs/dev/synchronisation-hors-ligne.md`.
"""

import gzip
import json
import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Store
from catalog.permissions import COST_VISIBLE_ROLES
from core.permissions import CASHIER, MANAGER, OWNER, HasRole

from . import pull, services
from .serializers import (
    MAX_OPERATIONS_PER_BATCH,
    PullResponseSerializer,
    PushRequestSerializer,
    PushResponseSerializer,
    StatusResponseSerializer,
)

SYNC_ROLES = [OWNER, MANAGER, CASHIER]
MIN_APP_VERSION = "0.1.0"
MAX_OFFLINE_DAYS = 7  # KOM-NF-015 : au-delà, l'appareil doit resynchroniser avant de continuer


def _error(code: str, message: str, status: int = 400) -> Response:
    return Response({"code": code, "detail": message}, status=status)


def _accepts_gzip(header: str) -> bool:
    for token in header.split(","):
        name, _, params = token.strip().partition(";")
        if name.strip().lower() not in ("gzip", "*"):
            continue
        quality = params.strip().removeprefix("q=").strip() if params.strip().startswith("q=") else "1"
        try:
            return float(quality) > 0
        except ValueError:
            return False
    return False


def _json_response(request, payload: dict) -> HttpResponse:
    """JSON, compressé en gzip si l'appareil l'accepte (réseaux 2G/3G, KOM-NF-014). Fait dans la vue
    car le middleware `GZipMiddleware` n'est pas (encore) activé dans les réglages."""
    body = json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False, separators=(",", ":")).encode()
    response = HttpResponse(content_type="application/json")
    response["Vary"] = "Accept-Encoding"
    if _accepts_gzip(request.headers.get("Accept-Encoding", "")):
        body = gzip.compress(body, compresslevel=6)
        response["Content-Encoding"] = "gzip"
    response.content = body
    return response


class PullView(APIView):
    """Delta des données de référence depuis un curseur."""

    permission_classes = [HasRole]
    allowed_roles = SYNC_ROLES

    @extend_schema(
        operation_id="sync_pull",
        parameters=[
            OpenApiParameter("since", OpenApiTypes.STR, description="Curseur du dernier appel."),
            OpenApiParameter("store", OpenApiTypes.UUID, description="Boutique de l'appareil (stock)."),
            OpenApiParameter("limit", OpenApiTypes.INT, description="Lignes par flux, 1 à 1000 (500)."),
        ],
        responses={200: PullResponseSerializer},
    )
    def get(self, request):
        """Renvoie les lignes de référence modifiées depuis `since` : boutiques, catégories, produits
        (désactivés inclus : `is_active` false = à retirer du catalogue local), clients avec leurs soldes
        par devise, niveaux de stock, et le taux de change courant. Réponse gzip si `Accept-Encoding: gzip`.
        Le coût d'achat n'est envoyé qu'au propriétaire et au gérant, jamais au caissier."""
        params = request.query_params
        try:
            limit = int(params.get("limit", pull.DEFAULT_LIMIT))
        except ValueError:
            return _error("invalid_limit", "« limit » doit être un entier.")
        if not 1 <= limit <= pull.MAX_LIMIT:
            return _error("invalid_limit", f"« limit » doit être compris entre 1 et {pull.MAX_LIMIT}.")
        store_id = None
        if params.get("store"):
            try:
                store_id = uuid.UUID(params["store"])
            except ValueError:
                return _error("invalid_store", "« store » n'est pas un UUID valide.")
            if not Store.objects.filter(pk=store_id).exists():
                return _error("store_not_found", "Boutique introuvable.", 404)
        try:
            result = pull.build_pull(
                since=params.get("since"), store_id=store_id, limit=limit,
                with_cost=request.membership.role in COST_VISIBLE_ROLES)
        except pull.InvalidCursor as exc:
            return _error("invalid_cursor", str(exc))
        return _json_response(request, result)


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class PushView(APIView):
    """Rejeu d'un lot d'opérations. Non atomique : chaque opération a sa propre transaction."""

    permission_classes = [HasRole]
    allowed_roles = SYNC_ROLES

    @extend_schema(operation_id="sync_push", request=PushRequestSerializer,
                   responses={200: PushResponseSerializer})
    def post(self, request):
        """Rejoue, dans l'ordre, les opérations enregistrées hors ligne (200 au plus par lot, sinon 400).

        Réponse 200 avec un résultat PAR opération : `applied`, `duplicate` (déjà appliquée : rien n'est
        refait, même `entity_id`) ou `rejected` (avec `error.code`). Une opération rejetée n'empêche pas
        les suivantes. Types : sale.create, sale.cancel, customer.create, customer.update,
        stock.movement, cash_session.open, cash_session.close."""
        operations = request.data.get("operations") if hasattr(request.data, "get") else None
        if isinstance(operations, list) and len(operations) > MAX_OPERATIONS_PER_BATCH:
            return _error("batch_too_large",
                          f"Un lot contient au plus {MAX_OPERATIONS_PER_BATCH} opérations.")
        serializer = PushRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        results = services.process_batch(
            device_id=data["device_id"], operations=data["operations"], tenant_id=request.tenant.id,
            user=request.user, role=request.membership.role)
        return Response({"results": results, "server_time": timezone.now()})


class StatusView(APIView):
    permission_classes = [HasRole]
    allowed_roles = SYNC_ROLES

    @extend_schema(operation_id="sync_status", responses={200: StatusResponseSerializer})
    def get(self, request):
        """Heure du serveur, version minimale de l'application et durée maximale hors ligne (jours) :
        l'appareil doit resynchroniser avant de continuer au-delà de `max_offline_days`."""
        return Response({
            "server_time": timezone.now(),
            "min_app_version": MIN_APP_VERSION,
            "max_offline_days": MAX_OFFLINE_DAYS,
        })
