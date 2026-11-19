"""Pages web de Komersia, montées sous /app/.

Les pages sont de simples coquilles HTML : toutes les données viennent de l'API /api/v1/, appelée
par app.js avec le jeton conservé dans localStorage. Le contrôle d'accès réel reste côté API.
"""

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "web"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="web:pos", permanent=False), name="home"),
    path("connexion/", views.page("login"), name="login"),
    path("inscription/", views.page("register"), name="register"),
    path("caisse/", views.page("pos"), name="pos"),
    path("produits/", views.page("products"), name="products"),
    path("stock/", views.page("stock"), name="stock"),
    path("clients/", views.page("customers"), name="customers"),
    path("taux/", views.page("rates"), name="rates"),
    path("ventes/", views.page("sales"), name="sales"),
    path("tableau-de-bord/", views.page("dashboard"), name="dashboard"),
    path("utilisateurs/", views.page("users"), name="users"),
    path("abonnement/", views.page("billing"), name="billing"),
]
