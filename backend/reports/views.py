import uuid

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Store
from core.permissions import ACCOUNTANT, MANAGER, OWNER, HasRole

from . import csv_export, services
from .serializers import (
    DailyReportSerializer,
    SummaryReportSerializer,
    parse_daily_params,
    parse_summary_params,
)

REPORT_ROLES = [OWNER, MANAGER, ACCOUNTANT]  # matrice section 19 : caissier et magasinier refusés

_STORE = OpenApiParameter("store", str, description="Identifiant de boutique (optionnel).")


def _store_param(request):
    """Boutique demandée (UUID) ; 400 si invalide ou introuvable dans l'entreprise (la RLS isole)."""
    raw = request.query_params.get("store")
    if not raw:
        return None
    try:
        store_id = uuid.UUID(raw)
    except ValueError:
        raise ValidationError({"store": "Identifiant invalide."}) from None
    if not Store.objects.filter(id=store_id).exists():
        raise ValidationError({"store": "Boutique introuvable."})
    return store_id


class _JSONOnlyNegotiation(DefaultContentNegotiation):
    """Ignore le paramètre `format` (réservé ici à l'export) : les erreurs restent en JSON."""

    def select_renderer(self, request, renderers, format_suffix=None):
        return JSONRenderer(), JSONRenderer.media_type


class _ReportView(APIView):
    permission_classes = [HasRole]
    allowed_roles = REPORT_ROLES
    content_negotiation_class = _JSONOnlyNegotiation


class DailyReportView(_ReportView):
    @extend_schema(
        parameters=[_STORE, OpenApiParameter("date", str, description="AAAA-MM-JJ (défaut : aujourd'hui).")],
        responses=DailyReportSerializer,
    )
    def get(self, request):
        store_id = _store_param(request)
        (date,) = parse_daily_params(request.query_params)
        return Response(services.daily_report(date, store_id))


class SummaryReportView(_ReportView):
    @extend_schema(
        parameters=[
            _STORE,
            OpenApiParameter("period", str, enum=["week", "month", "custom"]),
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
        ],
        responses=SummaryReportSerializer,
    )
    def get(self, request):
        store_id = _store_param(request)
        date_from, date_to = parse_summary_params(request.query_params)
        return Response(services.summary_report(date_from, date_to, store_id))


class ExportView(_ReportView):
    @extend_schema(
        parameters=[
            _STORE,
            OpenApiParameter("kind", str, enum=["daily", "summary"], required=True),
            OpenApiParameter("format", str, enum=["csv"]),
            OpenApiParameter("date", str),
            OpenApiParameter("period", str, enum=["week", "month", "custom"]),
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
        ],
        responses={(200, "text/csv"): bytes},
    )
    def get(self, request):
        params = request.query_params
        kind = params.get("kind")
        if kind not in ("daily", "summary"):
            raise ValidationError({"kind": "Valeur invalide (daily ou summary)."})
        if (params.get("format") or "csv") != "csv":
            raise ValidationError({"format": "Seul le format csv est disponible (PDF et Excel : à venir)."})
        store_id = _store_param(request)
        if kind == "daily":
            (date,) = parse_daily_params(params)
            data, name = services.daily_report(date, store_id), f"rapport-journalier-{date.isoformat()}"
        else:
            date_from, date_to = parse_summary_params(params)
            data = services.summary_report(date_from, date_to, store_id)
            name = f"rapport-synthese-{date_from.isoformat()}-{date_to.isoformat()}"
        store_name = Store.objects.get(id=store_id).name if store_id else None
        response = HttpResponse(
            csv_export.render(kind, data, store_name).encode("utf-8"), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
        return response
