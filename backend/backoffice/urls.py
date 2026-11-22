"""Routes de la console d'exploitation, montées sous /ops/ (voir komersia/urls.py)."""

from django.urls import path

from . import views

app_name = "ops"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("tenants/", views.tenant_list, name="tenant_list"),
    path("tenants/<uuid:tenant_id>/", views.tenant_detail, name="tenant_detail"),
    path("tenants/<uuid:tenant_id>/<slug:action>/", views.tenant_toggle, name="tenant_toggle"),
    path("tenants/<uuid:tenant_id>/flags/set/", views.tenant_flag, name="tenant_flag"),
    path("tenants/<uuid:tenant_id>/support/<str:kind>/", views.support_data, name="support_data"),
]
