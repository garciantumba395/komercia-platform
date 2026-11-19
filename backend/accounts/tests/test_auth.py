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


def test_login_of_deactivated_user_gives_explicit_message(tenant_a):
    from accounts.models import Membership

    Membership.objects.filter(user=tenant_a.user).update(is_active=False)
    res = APIClient().post("/api/v1/auth/login/",
                           {"phone": tenant_a.user.phone, "password": "MotDePasse-123!"})
    assert res.status_code == 403
    assert res.data["code"] == "account_disabled"
    assert "désactivé" in res.data["detail"]


def _create_member(api, phone, role=CASHIER):
    res = api.post("/api/v1/users/", {"full_name": "Employé", "phone": phone,
                                       "password": "Employe-Pass-1!", "role": role}, format="json")
    assert res.status_code == 201, res.data
    return res.data["id"]


def test_owner_changes_role_and_it_is_audited(tenant_a):
    from audit.models import AuditEntry
    from core.tenancy import tenant_context

    api = tenant_a.client()
    uid = _create_member(api, "+243994444441")
    res = api.patch(f"/api/v1/users/{uid}/", {"role": "manager"}, format="json")
    assert res.status_code == 200 and res.data["role"] == "manager"
    with tenant_context(tenant_a.tenant.id):
        entry = AuditEntry.objects.get(action="user.role_change")
    assert entry.before == {"role": "cashier"} and entry.after == {"role": "manager"}
    assert str(entry.user_id) == str(tenant_a.user.id)


def test_role_change_rules(tenant_a, tenant_b):
    api = tenant_a.client()
    uid = _create_member(api, "+243994444442")
    assert api.patch(f"/api/v1/users/{uid}/", {"role": "owner"}, format="json").status_code == 400
    assert api.patch(f"/api/v1/users/{uid}/", {"role": "superadmin"}, format="json").status_code == 400
    assert api.patch(f"/api/v1/users/{tenant_a.user.id}/", {"role": "cashier"},
                     format="json").status_code == 400  # propre rôle
    # Un utilisateur d'un autre tenant est introuvable.
    assert api.patch(f"/api/v1/users/{tenant_b.user.id}/", {"role": "cashier"},
                     format="json").status_code == 404


def test_only_owner_can_change_roles(tenant_a):
    api = tenant_a.client()
    uid = _create_member(api, "+243994444443", role="manager")
    login = APIClient().post("/api/v1/auth/login/", {"phone": "+243994444443",
                                                      "password": "Employe-Pass-1!"})
    manager = APIClient()
    manager.credentials(HTTP_AUTHORIZATION=f"Token {login.data['token']}")
    assert manager.patch(f"/api/v1/users/{uid}/", {"role": "cashier"},
                         format="json").status_code == 403


def test_single_tenant_installation_refuses_a_second_company(settings):
    settings.KOMERSIA_SINGLE_TENANT = True
    api = APIClient()
    first = api.post("/api/v1/auth/register/", REGISTER, format="json")
    assert first.status_code == 201
    second = api.post("/api/v1/auth/register/",
                      {**REGISTER, "phone": "+243992222333", "business_name": "Autre"}, format="json")
    assert second.status_code == 403
    assert second.data["code"] == "already_configured"
    # La connexion de l'entreprise existante reste possible.
    login = api.post("/api/v1/auth/login/", {"phone": REGISTER["phone"], "password": REGISTER["password"]})
    assert login.status_code == 200


def test_multi_tenant_default_allows_many_companies():
    api = APIClient()
    assert api.post("/api/v1/auth/register/", REGISTER, format="json").status_code == 201
    other = {**REGISTER, "phone": "+243992222444", "business_name": "Autre"}
    assert api.post("/api/v1/auth/register/", other, format="json").status_code == 201
