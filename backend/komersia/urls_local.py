"""URL de l'édition locale (Windows) : mêmes routes que `urls.py`, SAUF la console d'exploitation `/ops/`
et l'API de support `/api/v1/support/`, qui n'existent pas dans l'édition locale.

`desktop/tests/test_urls_parity.py` vérifie que cette liste reste synchronisée avec `urls.py` : toute route
ajoutée à `urls.py` doit être ajoutée ici (sauf ops/support).
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/catalog/", include("catalog.urls")),
    path("api/v1/stock/", include("stock.urls")),
    path("api/v1/customers/", include("customers.urls")),
    path("api/v1/sales/", include("sales.urls")),
    path("api/v1/reports/", include("reports.urls")),
    path("api/v1/currency/", include("currency.urls")),
    path("api/v1/audit/", include("audit.urls")),
    path("api/v1/sync/", include("sync.urls")),
    path("api/v1/billing/", include("billing.urls")),
    path("app/", include("web.urls")),
    path("", include("core.web_urls")),
]
