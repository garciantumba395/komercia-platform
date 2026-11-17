import csv
import uuid

from django.db.models import ProtectedError, Q
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from core.permissions import HasRole

from .importer import COLUMNS, ImportFileError, import_products
from .models import Category, Product
from .money import from_minor
from .permissions import ALL_ROLES, EXPORT_ROLES, WRITE_ROLES, can_see_cost
from .serializers import CategorySerializer, ProductSerializer

CRUD_ROLES = {
    "list": ALL_ROLES,
    "retrieve": ALL_ROLES,
    "create": WRITE_ROLES,
    "update": WRITE_ROLES,
    "partial_update": WRITE_ROLES,
    "destroy": WRITE_ROLES,
}

TRUE = {"1", "true", "yes", "oui"}
FALSE = {"0", "false", "no", "non"}


class CategoryViewSet(viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    permission_classes = [HasRole]
    allowed_roles_by_action = CRUD_ROLES
    pagination_class = None

    def get_queryset(self):
        return Category.objects.order_by("name")


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [HasRole]
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    allowed_roles_by_action = {
        **CRUD_ROLES,
        "import_csv": WRITE_ROLES,
        "export_csv": EXPORT_ROLES,
    }

    def get_queryset(self):
        qs = Product.objects.select_related("category").order_by("name", "id")
        params = self.request.query_params
        q = params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(sku__icontains=q)
                | Q(barcode__icontains=q)
                | Q(category_id__in=Category.objects.filter(name__icontains=q).values("id"))
            )
        if params.get("category"):
            try:
                qs = qs.filter(category_id=uuid.UUID(params["category"]))
            except ValueError:
                raise ValidationError({"category": "Identifiant invalide."}) from None
        if params.get("kind"):
            qs = qs.filter(kind=params["kind"])
        active = params.get("active", "").lower()
        if active in TRUE:
            qs = qs.filter(is_active=True)
        elif active in FALSE:
            qs = qs.filter(is_active=False)
        return qs

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {"detail": "Ce produit a un historique (stock, ventes) : désactivez-le au lieu de le supprimer."},
                status=status.HTTP_409_CONFLICT,
            )

    @action(detail=False, methods=["post"], url_path="import", url_name="import")
    def import_csv(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "Fichier CSV requis (champ « file »)."})
        if upload.size > 5 * 1024 * 1024:
            raise ValidationError({"file": "Fichier trop volumineux (5 Mo maximum)."})
        try:
            report = import_products(upload.read())
        except ImportFileError as exc:
            raise ValidationError({"file": str(exc)}) from None
        return Response(report)

    @action(detail=False, methods=["get"], url_path="export", url_name="export")
    def export_csv(self, request):
        show_cost = can_see_cost(request)
        columns = [c for c in COLUMNS if show_cost or c != "cost"]
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="produits.csv"'
        writer = csv.writer(response)
        writer.writerow(columns)
        for p in self.get_queryset().iterator():
            row = {
                "name": p.name, "sku": p.sku, "barcode": p.barcode,
                "category": p.category.name if p.category else "", "kind": p.kind, "unit": p.unit,
                "price": from_minor(p.price_minor, p.currency), "currency": p.currency,
                "cost": from_minor(p.cost_minor, p.currency),
            }
            writer.writerow([row[c] for c in columns])
        return response
