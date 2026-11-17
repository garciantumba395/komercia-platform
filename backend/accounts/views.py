from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import ACCOUNTANT, CASHIER, MANAGER, OWNER, STOREKEEPER, HasRole

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
            return Response({"detail": "Aucune entreprise active."}, status=403)
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


class StoreViewSet(viewsets.ModelViewSet):
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
        return Response(MemberSerializer(member).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        member = Membership.objects.filter(tenant=request.tenant, user_id=pk).first()
        if member is None:
            return Response(status=404)
        if member.user_id == request.user.id:
            return Response({"detail": "Vous ne pouvez pas modifier votre propre rôle."}, 400)
        if "is_active" in request.data:
            member.is_active = bool(request.data["is_active"])
        member.save()
        return Response(MemberSerializer(member).data)
