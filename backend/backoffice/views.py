"""Pages HTML de la console d'exploitation (serveur-rendues, sans dépendance externe)."""

import uuid

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from . import access, metrics, queries, services
from . import platform_audit as pa
from .flags import KNOWN_FLAGS

SESSION_SECONDS = 8 * 3600  # une journée de travail ; l'agent se reconnecte ensuite


@never_cache
def login_view(request):
    if access.is_agent(request.user):
        return redirect("/ops/")
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    context = {"next": next_url if next_url.startswith("/ops/") else "", "phone": ""}
    if request.method == "POST":
        phone = (request.POST.get("phone") or "").strip()[:30]
        password = request.POST.get("password") or ""
        context["phone"] = phone
        if access.is_locked(request, phone):
            context["error"] = "Trop de tentatives. Réessayez dans quelques minutes."
            return render(request, "backoffice/login.html", context, status=429)
        user = authenticate(request, username=phone, password=password)
        # Message unique : on ne révèle jamais si le numéro existe ou s'il s'agit d'un commerçant.
        if user is None or not access.is_agent(user):
            access.register_failure(request, phone)
            context["error"] = "Identifiants invalides."
            return render(request, "backoffice/login.html", context, status=400)
        access.clear_failures(request, phone)
        login(request, user)  # change la clé de session (anti-fixation)
        request.session.set_expiry(SESSION_SECONDS)
        return redirect(access.safe_next(request, request.POST.get("next")))
    return render(request, "backoffice/login.html", context)


@require_POST
def logout_view(request):
    logout(request)
    return redirect(access.LOGIN_URL)


@require_safe
@access.ops_required
def dashboard(request):
    return render(request, "backoffice/dashboard.html", {"m": metrics.platform_metrics()})


@require_safe
@access.ops_required
def tenant_list(request):
    search = request.GET.get("q", "")
    state = request.GET.get("state", "")
    status = request.GET.get("status", "")
    page = Paginator(queries.tenant_list_queryset(search, state, status), queries.PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    from billing.models import Subscription

    return render(request, "backoffice/tenant_list.html", {
        "page": page, "q": search, "state": state, "status": status, "states": Subscription.STATES,
    })


def _tenant_or_404(tenant_id):
    overview = queries.tenant_overview(tenant_id)
    if overview is None:
        raise Http404
    return overview


@require_safe
@access.ops_required
def tenant_detail(request, tenant_id: uuid.UUID):
    overview = _tenant_or_404(tenant_id)
    grant = services.active_grant(tenant_id)
    flags = [{"key": k, "label": label, "enabled": overview["flags"].get(k)} for k, label in KNOWN_FLAGS.items()]
    return render(request, "backoffice/tenant_detail.html", {**overview, "grant": grant, "flag_rows": flags})


@require_http_methods(["GET", "POST"])
@access.ops_required
def tenant_toggle(request, tenant_id: uuid.UUID, action: str):
    """GET : page de confirmation avec motif ; POST : applique (motif et confirmation obligatoires)."""
    if action not in ("suspend", "reactivate"):
        raise Http404
    overview = _tenant_or_404(tenant_id)
    suspend = action == "suspend"
    context = {"tenant": overview["tenant"], "suspend": suspend, "reason": ""}
    if request.method == "POST":
        context["reason"] = request.POST.get("reason", "")
        try:
            if request.POST.get("confirm") != "yes":
                raise services.ConsoleError("Cochez la case de confirmation.")
            services.set_tenant_active(request, tenant_id, active=not suspend, reason=context["reason"])
        except services.ConsoleError as exc:
            context["error"] = str(exc)
            return render(request, "backoffice/tenant_toggle.html", context, status=400)
        messages.success(request, "Entreprise suspendue." if suspend else "Entreprise réactivée.")
        return redirect("ops:tenant_detail", tenant_id=tenant_id)
    return render(request, "backoffice/tenant_toggle.html", context)


@require_POST
@access.platform_owner_required
def tenant_flag(request, tenant_id: uuid.UUID):
    _tenant_or_404(tenant_id)
    try:
        services.set_flag(
            request, tenant_id, request.POST.get("key", ""), request.POST.get("enabled") == "on",
            request.POST.get("reason", ""),
        )
        messages.success(request, "Fonction mise à jour.")
    except services.ConsoleError as exc:
        messages.error(request, str(exc))
    return redirect("ops:tenant_detail", tenant_id=tenant_id)


SUPPORT_PAGES = {
    "sales": ("Ventes récentes", queries.detail_sales),
    "products": ("Produits", queries.detail_products),
    "customers": ("Clients", queries.detail_customers),
}


@require_safe
@access.ops_required
def support_data(request, tenant_id: uuid.UUID, kind: str):
    """Données détaillées d'une entreprise : LECTURE SEULE, avec consentement valide, chaque consultation journalisée."""
    if kind not in SUPPORT_PAGES:
        raise Http404
    overview = _tenant_or_404(tenant_id)
    grant = services.active_grant(tenant_id)
    if grant is None:
        # Aucune lecture de données détaillées sans consentement valide (absent, expiré ou révoqué).
        return HttpResponseForbidden(
            render(request, "backoffice/forbidden.html", {
                "message": "Aucun accès de support valide : le propriétaire de l'entreprise doit d'abord l'accorder.",
                "back": f"/ops/tenants/{tenant_id}/",
            }).content
        )
    title, fetch = SUPPORT_PAGES[kind]
    # Journaliser AVANT de lire : si l'écriture de l'audit échoue, aucune donnée n'est affichée.
    pa.log_platform_action(
        request, tenant_id, pa.ACTION_SUPPORT_VIEW,
        after={"page": kind, "agent": request.user.full_name, "grant_id": str(grant.id)},
        reason=grant.reason,
    )
    rows = fetch(tenant_id)
    return render(request, f"backoffice/support_{kind}.html", {
        "tenant": overview["tenant"], "title": title, "rows": rows, "grant": grant,
        "totals": queries.month_sales_totals(tenant_id) if kind == "sales" else None,
    })


