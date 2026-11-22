import re

import pytest
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.views import serve
from django.test import RequestFactory
from django.urls import reverse

PAGES = [
    "login", "register", "pos", "products", "stock", "customers", "rates", "sales", "dashboard", "users",
    "billing",
]


@pytest.mark.django_db
@pytest.mark.parametrize("name", PAGES)
def test_page_responds_with_french_title(client, name):
    response = client.get(reverse(f"web:{name}"))
    assert response.status_code == 200
    html = response.content.decode()
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert '<html lang="fr">' in html
    title = re.search(r"<title>(.+?)</title>", html).group(1)
    assert title.endswith("– Komersia.App") and len(title) > len("– Komersia.App") + 2
    assert html.count("<main") == 1 and 'id="view"' in html


@pytest.mark.django_db
def test_home_redirects_to_pos(client):
    response = client.get("/app/")
    assert response.status_code == 302 and response["Location"] == reverse("web:pos")


@pytest.mark.parametrize("name", ["app.css", "app.js", "pos.js", "pages.js", "billing.js"])
def test_static_files_are_found_and_served(name):
    assert finders.find(f"web/{name}")
    response = serve(RequestFactory().get(f"/static/web/{name}"), f"web/{name}", insecure=True)
    assert response.status_code == 200 and len(b"".join(response.streaming_content)) > 500
