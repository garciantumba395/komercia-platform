import pytest
from rest_framework.test import APIClient

from core.permissions import CASHIER

pytestmark = pytest.mark.django_db

REGISTER = {
    "full_name": "Jean Mukendi", "phone": "+243991111111", "password": "Sup3r-Secret!",
    "business_name": "Chez Jean", "sector": "retail", "base_currency": "CDF",
}


def test_register_creates_tenant_store_and_owner():
    res = APIClient().post("/api/v1/auth/register/", REGISTER, format="json")
    assert res.status_code == 201
    assert res.data["role"] == "owner" and res.data["token"]
    assert res.data["store"]["name"] == "Chez Jean"


def test_register_rejects_duplicate_phone_and_weak_password():
    api = APIClient()
    api.post("/api/v1/auth/register/", REGISTER, format="json")
    assert api.post("/api/v1/auth/register/", REGISTER, format="json").status_code == 400
    weak = {**REGISTER, "phone": "+243992222222", "password": "12345678"}
    assert api.post("/api/v1/auth/register/", weak, format="json").status_code == 400


def test_login_and_me():
    api = APIClient()
    api.post("/api/v1/auth/register/", REGISTER, format="json")
    bad = api.post("/api/v1/auth/login/", {"phone": REGISTER["phone"], "password": "x"})
    assert bad.status_code == 400
    ok = api.post("/api/v1/auth/login/",
                  {"phone": REGISTER["phone"], "password": REGISTER["password"]})
    assert ok.status_code == 200
    api.credentials(HTTP_AUTHORIZATION=f"Token {ok.data['token']}")
    me = api.get("/api/v1/auth/me/")
    assert me.status_code == 200 and me.data["tenant"]["name"] == "Chez Jean"


def test_requires_authentication():
    assert APIClient().get("/api/v1/stores/").status_code == 401


def test_owner_manages_users_and_cashier_is_restricted(tenant_a):
    api = tenant_a.client()
    created = api.post("/api/v1/users/", {"full_name": "Marie", "phone": "+243993333333",
                                           "password": "Caissi3r-Pass!", "role": CASHIER},
                       format="json")
    assert created.status_code == 201
    login = APIClient().post("/api/v1/auth/login/", {"phone": "+243993333333",
                                                      "password": "Caissi3r-Pass!"})
    cashier = APIClient()
    cashier.credentials(HTTP_AUTHORIZATION=f"Token {login.data['token']}")
    assert cashier.get("/api/v1/stores/").status_code == 200
    assert cashier.post("/api/v1/stores/", {"name": "Autre"}, format="json").status_code == 403
    assert cashier.get("/api/v1/users/").status_code == 403
    # Désactivation d'un utilisateur par le propriétaire
    uid = created.data["id"]
    assert api.patch(f"/api/v1/users/{uid}/", {"is_active": False}, format="json").status_code == 200
    assert cashier.get("/api/v1/stores/").status_code == 401 or \
        cashier.get("/api/v1/stores/").status_code == 403
