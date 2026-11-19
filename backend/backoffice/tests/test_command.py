"""Commande create_platform_admin et refus de l'API commerçant pour un agent sans entreprise."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import User

from .helpers import DB

pytestmark = DB


def test_creates_staff_user_without_membership(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "Agent-Secret-9731!")
    call_command("create_platform_admin", "--phone", "+243810000100", "--name", "Agent Cent")
    user = User.objects.get(phone="+243810000100")
    assert user.is_staff and user.is_active and not user.is_superuser
    assert user.check_password("Agent-Secret-9731!")
    assert not user.memberships.exists()


def test_owner_flag(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "Agent-Secret-9731!")
    call_command("create_platform_admin", "--phone", "+243810000101", "--name", "Chef", "--owner")
    assert User.objects.get(phone="+243810000101").is_superuser


def test_password_is_never_an_argument():
    with pytest.raises(CommandError):
        call_command("create_platform_admin", "--phone", "+243810000102", "--name", "X", "--password", "abc")


def test_weak_password_and_duplicate_rejected(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "123")
    with pytest.raises(CommandError):
        call_command("create_platform_admin", "--phone", "+243810000103", "--name", "X")
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "Agent-Secret-9731!")
    call_command("create_platform_admin", "--phone", "+243810000104", "--name", "X")
    with pytest.raises(CommandError):
        call_command("create_platform_admin", "--phone", "+243810000104", "--name", "Y")


def test_interactive_prompt(monkeypatch):
    monkeypatch.delenv("PLATFORM_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "Agent-Secret-9731!")
    call_command("create_platform_admin", "--phone", "+243810000105", "--name", "Interactif")
    assert User.objects.get(phone="+243810000105").check_password("Agent-Secret-9731!")


def test_merchant_api_refuses_platform_agent(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "Agent-Secret-9731!")
    call_command("create_platform_admin", "--phone", "+243810000106", "--name", "Agent")
    user = User.objects.get(phone="+243810000106")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    for url in ("/api/v1/auth/me/", "/api/v1/sales/", "/api/v1/catalog/products/"):
        response = client.get(url)
        assert response.status_code == 401
        assert "Aucune entreprise active pour cet utilisateur" in response.json()["detail"]
    login = APIClient().post("/api/v1/auth/login/", {"phone": user.phone, "password": "Agent-Secret-9731!"},
                             format="json")
    assert login.status_code == 403 and login.json()["code"] == "no_active_tenant"
