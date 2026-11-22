"""URLconf de test : les routes de l'application + l'API de consentement (`api/v1/support/`).

La route `api/v1/support/` doit être ajoutée à `komersia/urls.py` (non modifiable par le module backoffice) ;
en attendant, les tests la montent ici.
"""

from django.urls import include, path

from komersia.urls import urlpatterns as root_patterns

urlpatterns = [path("api/v1/support/", include("backoffice.api_urls")), *root_patterns]
