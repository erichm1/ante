from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from integrations.models import ICON_IMAGE_EXTENSIONS, ICON_IMAGE_MAX_SIZE_MB, validate_icon_image_size


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

    def __str__(self):
        return f"Profile for {self.user.username}"
