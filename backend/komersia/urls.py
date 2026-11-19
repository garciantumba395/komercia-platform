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
    path("api/v1/support/", include("backoffice.api_urls")),
    path("ops/", include("backoffice.urls")),
    path("app/", include("web.urls")),
    path("", include("core.web_urls")),
]
