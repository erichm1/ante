from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from integrations.models import ICON_IMAGE_EXTENSIONS, ICON_IMAGE_MAX_SIZE_MB, validate_icon_image_size


class Department(models.Model):
    """Where a person sits in the organisation. Purely organisational (a label for the users list and for
    reporting) — what someone may *do* comes from their access groups and per-user overrides."""

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AccessGroup(models.Model):
    """A named bundle of module permissions — each module is simply on or off (see accounts/permissions.py
    for the module list). A user's access is the union of their groups' modules, plus anything switched on
    just for them, minus anything switched off just for them. `is_default` groups are given to every new user."""

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    modules = models.JSONField(default=list, blank=True, help_text="Keys of the modules this group switches on.")
    is_default = models.BooleanField(default=False, help_text="Automatically added to every newly created user.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Profile(models.Model):
    """The one-per-user extra fields auth.User doesn't have — first/last
    name and email stay on User itself (edited in place on request.user by
    accounts/views.py::profile) rather than duplicated here. avatar/
    company_icon are plain FileFields (not ImageField), same reasoning as
    Integration.icon_image: Pillow isn't a declared project dependency."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    avatar = models.FileField(
        upload_to="accounts/avatars/%Y/%m/", null=True, blank=True,
        validators=[FileExtensionValidator(allowed_extensions=ICON_IMAGE_EXTENSIONS), validate_icon_image_size],
        help_text=f"Personal profile photo (PNG/JPG/GIF/WEBP/SVG, up to {ICON_IMAGE_MAX_SIZE_MB}MB).",
    )
    company_name = models.CharField(max_length=200, blank=True)
    company_document = models.CharField(max_length=50, blank=True, help_text="Tax/registration id, e.g. CNPJ.")
    company_icon = models.FileField(
        upload_to="accounts/company_icons/%Y/%m/", null=True, blank=True,
        validators=[FileExtensionValidator(allowed_extensions=ICON_IMAGE_EXTENSIONS), validate_icon_image_size],
        help_text=f"Company logo, separate from the personal avatar above (up to {ICON_IMAGE_MAX_SIZE_MB}MB).",
    )

    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="members")
    groups = models.ManyToManyField(AccessGroup, blank=True, related_name="members")
    allowed_modules = models.JSONField(
        default=list, blank=True, help_text="Modules switched ON for this user regardless of their groups.")
    denied_modules = models.JSONField(
        default=list, blank=True, help_text="Modules switched OFF for this user even if a group grants them.")

    def __str__(self):
        return f"Profile for {self.user.username}"
