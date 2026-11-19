from django.views.generic import TemplateView

# clé de page -> (titre français, page publique ?)
PAGES = {
    "login": ("Connexion", True),
    "register": ("Créer mon compte", True),
    "pos": ("Caisse", False),
    "products": ("Produits", False),
    "stock": ("Stock", False),
    "customers": ("Clients et dettes", False),
    "rates": ("Taux de change", False),
    "sales": ("Ventes du jour", False),
    "dashboard": ("Tableau de bord", False),
    "users": ("Utilisateurs", False),
    "billing": ("Abonnement", False),
}


class PageView(TemplateView):
    """Coquille HTML d'une page ; le contenu est construit par app.js."""

    template_name = "web/page.html"
    page = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        title, public = PAGES[self.page]
        context.update(page=self.page, page_title=title, public=public)
        return context


def page(key: str):
    return PageView.as_view(page=key)
