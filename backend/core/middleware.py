from .tenancy import reset_tenant


class TenantContextMiddleware:
    """Garantit qu'aucun contexte de tenant ne fuit d'une requête à la suivante."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        reset_tenant()
        try:
            return self.get_response(request)
        finally:
            reset_tenant()
