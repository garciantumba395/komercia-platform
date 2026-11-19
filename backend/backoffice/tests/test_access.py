"""Accès à la console : réservé aux agents is_staff ET actifs, par session, CSRF, limitation des tentatives."""

import pytest
from django.test import Client

from accounts.models import User

from .helpers import AGENT_PASSWORD, DB, agent_client, make_agent

pytestmark = DB

PAGES = ["/ops/", "/ops/tenants/"]


def _denied(response):
    return response.status_code == 403 or (response.status_code == 302 and "/ops/login/" in response["Location"])


@pytest.mark.parametrize("url", PAGES)
def test_anonymous_redirected_to_login(client, url):
    response = client.get(url)
    assert response.status_code == 302 and response["Location"].startswith("/ops/login/")


@pytest.mark.parametrize("url", PAGES)
def test_merchant_owner_denied(tenant_a, url):
    client = Client()
    client.force_login(tenant_a.user)  # propriétaire d'une entreprise, mais is_staff=False
    assert client.get(url).status_code == 403


@pytest.mark.parametrize("url", PAGES)
def test_merchant_even_with_staff_false_explicit_denied(tenant_a, url):
    User.objects.filter(pk=tenant_a.user.pk).update(is_staff=False)
    client = Client()
    client.force_login(User.objects.get(pk=tenant_a.user.pk))
    assert _denied(client.get(url))


@pytest.mark.parametrize("url", PAGES)
def test_inactive_staff_denied(url):
    agent = make_agent()
    client = agent_client(agent)
    User.objects.filter(pk=agent.pk).update(is_active=False)
    assert _denied(client.get(url))


@pytest.mark.parametrize("url", PAGES)
def test_agent_allowed(url):
    assert agent_client().get(url).status_code == 200


@pytest.mark.parametrize("url", PAGES)
def test_merchant_api_token_refused(tenant_a, url):
    client = Client(HTTP_AUTHORIZATION=f"Token {tenant_a.token.key}")
    assert _denied(client.get(url))
    # Un jeton d'agent (staff) n'ouvre pas non plus la console : seule la session compte.
    from rest_framework.authtoken.models import Token

    token = Token.objects.create(user=make_agent("+243810000009"))
    assert _denied(Client(HTTP_AUTHORIZATION=f"Token {token.key}").get(url))


def test_login_agent_ok_and_session_expiry_set(client):
    agent = make_agent()
    response = client.post("/ops/login/", {"phone": agent.phone, "password": AGENT_PASSWORD})
    assert response.status_code == 302 and response["Location"] == "/ops/"
    assert client.get("/ops/").status_code == 200
    assert 0 < client.session.get_expiry_age() <= 8 * 3600


def test_login_rejects_merchant_even_with_valid_password(tenant_a, client):
    response = client.post("/ops/login/", {"phone": tenant_a.user.phone, "password": "MotDePasse-123!"})
    assert response.status_code == 400
    assert "Identifiants invalides" in response.content.decode()
    assert client.get("/ops/").status_code == 302  # aucune session ouverte


def test_login_wrong_password_and_unknown_user_same_message(client):
    agent = make_agent()
    wrong = client.post("/ops/login/", {"phone": agent.phone, "password": "mauvais"})
    unknown = client.post("/ops/login/", {"phone": "+243000000000", "password": "mauvais"})
    assert wrong.status_code == unknown.status_code == 400
    assert "Identifiants invalides" in wrong.content.decode()


def test_login_inactive_agent_refused(client):
    agent = make_agent()
    User.objects.filter(pk=agent.pk).update(is_active=False)
    assert client.post("/ops/login/", {"phone": agent.phone, "password": AGENT_PASSWORD}).status_code == 400


def test_login_throttled_after_repeated_failures(client):
    agent = make_agent()
    for _ in range(5):
        assert client.post("/ops/login/", {"phone": agent.phone, "password": "mauvais"}).status_code == 400
    # Même avec le bon mot de passe : verrouillé.
    response = client.post("/ops/login/", {"phone": agent.phone, "password": AGENT_PASSWORD})
    assert response.status_code == 429
    assert client.get("/ops/").status_code == 302


def test_login_open_redirect_blocked(client):
    agent = make_agent()
    response = client.post("/ops/login/", {"phone": agent.phone, "password": AGENT_PASSWORD,
                                           "next": "https://evil.example/x"})
    assert response["Location"] == "/ops/"


def test_login_form_has_csrf_and_post_without_token_rejected():
    agent = make_agent()
    client = Client(enforce_csrf_checks=True)
    page = client.get("/ops/login/")
    assert "csrfmiddlewaretoken" in page.content.decode()
    assert client.post("/ops/login/", {"phone": agent.phone, "password": AGENT_PASSWORD}).status_code == 403


def test_logout_requires_post_and_csrf():
    agent = make_agent()
    client = agent_client(agent, enforce_csrf_checks=True)
    assert client.get("/ops/logout/").status_code == 405
    assert client.post("/ops/logout/").status_code == 403  # CSRF
    assert client.get("/ops/").status_code == 200  # toujours connecté


def test_logout_ends_session():
    client = agent_client()
    assert client.post("/ops/logout/").status_code == 302
    assert client.get("/ops/").status_code == 302


def test_pages_not_cacheable():
    response = agent_client().get("/ops/")
    assert "no-store" in response["Cache-Control"] or "no-cache" in response["Cache-Control"]
