from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from audit import services as audit
from audit.mixins import AuditedMixin
from core.permissions import ACCOUNTANT, CASHIER, MANAGER, OWNER, ROLES, STOREKEEPER, HasRole

from .models import Membership, Store, User
from .serializers import (
    LoginSerializer,
    MemberCreateSerializer,
    MemberSerializer,
    RegisterSerializer,
    StoreSerializer,
)
from .services import issue_token, register_merchant


class RegisterView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, tenant, store = register_merchant(**serializer.validated_data)
        return Response(
            {
                "token": issue_token(user),
                "tenant": {"id": str(tenant.id), "name": tenant.name,
                           "base_currency": tenant.base_currency},
                "store": {"id": str(store.id), "name": store.name},
                "role": OWNER,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        membership = user.memberships.filter(is_active=True).order_by("created_at").first()
        if membership is None:
            has_membership = user.memberships.exists()
            return Response(
                {"detail": "Compte désactivé. Contactez le propriétaire de l'entreprise."
                 if has_membership else "Aucune entreprise active.",
                 "code": "account_disabled" if has_membership else "no_active_tenant"},
                status=403,
            )
        return Response(
            {"token": issue_token(user), "role": membership.role,
             "tenant": {"id": str(membership.tenant_id), "name": membership.tenant.name,
                        "base_currency": membership.tenant.base_currency}}
        )


class MeView(APIView):
    def get(self, request):
        m = request.membership
        return Response(
            {"id": str(request.user.id), "full_name": request.user.full_name,
             "phone": request.user.phone, "role": m.role,
             "tenant": {"id": str(m.tenant_id), "name": m.tenant.name,
                        "base_currency": m.tenant.base_currency}}
        )


class StoreViewSet(AuditedMixin, viewsets.ModelViewSet):
    audit_entity_type = "store"
    serializer_class = StoreSerializer
    permission_classes = [HasRole]
    allowed_roles_by_action = {
        "list": [OWNER, MANAGER, CASHIER, STOREKEEPER, ACCOUNTANT],
        "retrieve": [OWNER, MANAGER, CASHIER, STOREKEEPER, ACCOUNTANT],
        "create": [OWNER],
        "update": [OWNER],
        "partial_update": [OWNER],
        "destroy": [OWNER],
    }

    def get_queryset(self):
        return Store.objects.order_by("name")  # filtré par la RLS


class MemberViewSet(viewsets.ViewSet):
    """Gestion des utilisateurs du commerçant (propriétaire uniquement, KOM-005)."""

    permission_classes = [HasRole]
    allowed_roles = [OWNER]

    def list(self, request):
        members = Membership.objects.filter(tenant=request.tenant).select_related("user")
        return Response(MemberSerializer(members, many=True).data)

    @transaction.atomic
    def create(self, request):
        serializer = MemberCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = User.objects.create_user(phone=data["phone"], password=data["password"],
                                        full_name=data["full_name"])
        member = Membership.objects.create(user=user, tenant=request.tenant, role=data["role"])
        audit.log("user.create", "user", user.id, request=request,
                  after={"user_id": user.id, "full_name": user.full_name, "phone": user.phone,
                         "role": member.role, "is_active": member.is_active})
        return Response(MemberSerializer(member).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        member = Membership.objects.filter(tenant=request.tenant, user_id=pk).first()
        if member is None:
            return Response(status=404)
        if member.user_id == request.user.id:
            return Response({"detail": "Vous ne pouvez pas modifier votre propre rôle."}, 400)
        before = {"role": member.role, "is_active": member.is_active}
        if "role" in request.data:
            new_role = request.data["role"]
            assignable = [code for code, _label in ROLES if code != OWNER]
            if member.role == OWNER:
                return Response({"detail": "Le rôle d'un propriétaire ne peut pas être modifié."}, 400)
            if new_role == OWNER:
                return Response({"detail": "Le transfert de propriété n'est pas disponible."}, 400)
            if new_role not in assignable:
                return Response({"detail": "Rôle inconnu."}, 400)
            member.role = new_role
        if "is_active" in request.data:
            member.is_active = bool(request.data["is_active"])
        member.save()
        after = {"role": member.role, "is_active": member.is_active}
        if after["role"] != before["role"]:
            audit.log("user.role_change", "user", member.user_id, request=request,
                      before={"role": before["role"]}, after={"role": after["role"]})
        if after["is_active"] != before["is_active"]:
            audit.log("user.reactivate" if after["is_active"] else "user.deactivate", "user",
                      member.user_id, request=request,
                      before={"is_active": before["is_active"]}, after={"is_active": after["is_active"]})
        return Response(MemberSerializer(member).data)
