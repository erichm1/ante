"""The user-administration API behind the "Administration" tabs of the profile page: users, access groups,
departments and the module list. Administrators only (is_staff / is_superuser) — enforced here and, before
the request even gets this far, by ModuleAccessMiddleware (ADMIN_ONLY_PREFIXES).

Guard rails, because an administration screen is the one place you can lock yourself out of the app:
  * you cannot deactivate, demote or delete your own account;
  * the last active administrator can never be deactivated, demoted or deleted;
  * only a superuser can grant or take away superuser status, or touch a superuser's account.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from rest_framework import permissions as drf_permissions
from rest_framework import serializers, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from . import permissions
from .models import AccessGroup, Department, Profile

User = get_user_model()


class IsAppAdmin(drf_permissions.BasePermission):
    message = "Only administrators can manage users, groups and departments."

    def has_permission(self, request, view):
        return permissions.is_admin(request.user)


def _clean_modules(value, field):
    if not isinstance(value, list) or not all(isinstance(k, str) for k in value):
        raise serializers.ValidationError("Must be a list of module keys.")
    unknown = sorted(set(value) - permissions.ALL_KEYS)
    if unknown:
        raise serializers.ValidationError(f"Unknown module(s): {', '.join(unknown)}.")
    return [m.key for m in permissions.MODULES if m.key in set(value)]        # canonical order, no duplicates


def _active_admins():
    return User.objects.filter(is_active=True).filter(Q(is_staff=True) | Q(is_superuser=True))


# ── modules ──────────────────────────────────────────────────────────────────────────────────────────────────
class ModuleListView(APIView):
    permission_classes = [IsAppAdmin]

    def get(self, request):
        return Response([{"key": m.key, "label": m.label, "icon": m.icon, "description": m.description} for m in permissions.MODULES])


# ── departments ──────────────────────────────────────────────────────────────────────────────────────────────
class DepartmentSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Department
        fields = ["id", "name", "description", "member_count"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A name is required.")
        return value


class DepartmentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAppAdmin]
    serializer_class = DepartmentSerializer
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        return Department.objects.annotate(member_count=Count("members")).order_by("name")


# ── access groups ────────────────────────────────────────────────────────────────────────────────────────────
class AccessGroupSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AccessGroup
        fields = ["id", "name", "description", "modules", "is_default", "member_count"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A name is required.")
        return value

    def validate_modules(self, value):
        return _clean_modules(value, "modules")


class AccessGroupViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAppAdmin]
    serializer_class = AccessGroupSerializer
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        return AccessGroup.objects.annotate(member_count=Count("members")).order_by("name")


# ── users ────────────────────────────────────────────────────────────────────────────────────────────────────
class UserAdminSerializer(serializers.ModelSerializer):
    """A user with the profile parts an administrator manages, flattened into one object."""

    password = serializers.CharField(write_only=True, required=False, allow_blank=True, style={"input_type": "password"})
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all(), allow_null=True, required=False)
    groups = serializers.PrimaryKeyRelatedField(queryset=AccessGroup.objects.all(), many=True, required=False)
    allowed_modules = serializers.ListField(child=serializers.CharField(), required=False)
    denied_modules = serializers.ListField(child=serializers.CharField(), required=False)
    effective_modules = serializers.SerializerMethodField()
    is_you = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "password", "is_active", "is_staff", "is_superuser",
                  "last_login", "date_joined", "department", "groups", "allowed_modules", "denied_modules",
                  "effective_modules", "is_you"]
        read_only_fields = ["last_login", "date_joined"]

    # -- read side
    def to_representation(self, user):
        data = super().to_representation(user)
        profile = getattr(user, "profile", None)
        data["department"] = profile.department_id if profile else None
        data["groups"] = sorted(profile.groups.values_list("pk", flat=True)) if profile else []
        data["allowed_modules"] = list(profile.allowed_modules or []) if profile else []
        data["denied_modules"] = list(profile.denied_modules or []) if profile else []
        return data

    def get_effective_modules(self, user):
        return [m.key for m in permissions.MODULES if m.key in permissions.effective_modules(user)]

    def get_is_you(self, user):
        return user.pk == self.context["request"].user.pk

    # -- validation
    def validate_allowed_modules(self, value):
        return _clean_modules(value, "allowed_modules")

    def validate_denied_modules(self, value):
        return _clean_modules(value, "denied_modules")

    def validate_username(self, value):
        return value.strip()

    def validate(self, attrs):
        actor, target = self.context["request"].user, self.instance
        if target is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": "A password is required for a new user."})
        password = attrs.get("password")
        if password:
            try:
                validate_password(password, user=target or User(username=attrs.get("username", "")))
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"password": list(exc.messages)})

        both = set(attrs.get("allowed_modules", [])) & set(attrs.get("denied_modules", []))
        if both:
            raise serializers.ValidationError({"denied_modules": f"A module can't be both on and off: {', '.join(sorted(both))}."})

        # Who may change what.
        if "is_superuser" in attrs and attrs["is_superuser"] != (target.is_superuser if target else False) and not actor.is_superuser:
            raise serializers.ValidationError({"is_superuser": "Only a superuser can grant or remove superuser status."})
        if target is not None and target.is_superuser and not actor.is_superuser:
            raise serializers.ValidationError("Only a superuser can change a superuser's account.")
        if target is None and attrs.get("is_superuser") and not actor.is_superuser:
            raise serializers.ValidationError({"is_superuser": "Only a superuser can create a superuser."})

        if target is not None:
            will_be_admin = attrs.get("is_staff", target.is_staff) or attrs.get("is_superuser", target.is_superuser)
            will_be_active = attrs.get("is_active", target.is_active)
            losing = permissions.is_admin(target) and target.is_active and not (will_be_admin and will_be_active)
            if losing:
                if target.pk == actor.pk:
                    raise serializers.ValidationError("You can't remove your own administrator access or deactivate your own account.")
                if not _active_admins().exclude(pk=target.pk).exists():
                    raise serializers.ValidationError("This is the last active administrator — the app would be left without one.")
        return attrs

    # -- write side
    @transaction.atomic
    def create(self, validated):
        profile_bits = self._split_profile(validated)
        password = validated.pop("password")
        user = User(**validated)
        user.set_password(password)
        user.save()                                       # the post_save signal creates the Profile and adds default groups
        self._apply_profile(user, profile_bits)
        return user

    @transaction.atomic
    def update(self, user, validated):
        profile_bits = self._split_profile(validated)
        password = validated.pop("password", None)
        for attr, value in validated.items():
            setattr(user, attr, value)
        if password:
            user.set_password(password)
        user.save()
        self._apply_profile(user, profile_bits)
        return user

    @staticmethod
    def _split_profile(validated):
        return {k: validated.pop(k) for k in ("department", "groups", "allowed_modules", "denied_modules") if k in validated}

    @staticmethod
    def _apply_profile(user, bits):
        try:
            profile = user.profile                        # the same instance the response is built from
        except Profile.DoesNotExist:
            profile, _ = Profile.objects.get_or_create(user=user)
        for key in ("department", "allowed_modules", "denied_modules"):
            if key in bits:
                setattr(profile, key, bits[key])
        profile.save()
        if "groups" in bits:
            profile.groups.set(bits["groups"])
        if hasattr(user, "_ante_modules"):
            del user._ante_modules                        # the memoised answer from effective_modules() is stale now


class UserAdminViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAppAdmin]
    serializer_class = UserAdminSerializer
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        qs = User.objects.select_related("profile").prefetch_related("profile__groups").order_by("username")
        p = self.request.query_params
        if p.get("q"):
            q = p["q"].strip()
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
        if p.get("department"):
            qs = qs.filter(profile__department_id=p["department"])
        if p.get("group"):
            qs = qs.filter(profile__groups__pk=p["group"])
        return qs

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user.pk == request.user.pk:
            return Response({"detail": "You can't delete your own account."}, status=status.HTTP_400_BAD_REQUEST)
        if user.is_superuser and not request.user.is_superuser:
            return Response({"detail": "Only a superuser can delete a superuser's account."}, status=status.HTTP_400_BAD_REQUEST)
        if permissions.is_admin(user) and user.is_active and not _active_admins().exclude(pk=user.pk).exists():
            return Response({"detail": "This is the last active administrator — the app would be left without one."},
                            status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)
