import secrets

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models

from connections.models import Connection

ICON_IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "svg"]
ICON_IMAGE_MAX_SIZE_MB = 1


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def validate_icon_image_size(file):
    max_bytes = ICON_IMAGE_MAX_SIZE_MB * 1024 * 1024
    if file.size > max_bytes:
        raise ValidationError(f"Logo image must be {ICON_IMAGE_MAX_SIZE_MB}MB or smaller.")


class Integration(models.Model):
    """
    A catalog entry in the App Store — a pre-packaged connector to an
    external service (Shopify, an internal ERP, a generic custom API, ...),
    created by an admin so every user can see and install it instead of
    hand-filling a raw Connection.

    Doesn't do anything by itself; "installing" one (see InstalledIntegration)
    creates a real `connections.Connection` under the hood, pre-filled with
    this integration's auth type and, for OAuth2, the app's own client
    registration with that provider.

    For auth_type=OAUTH2, the oauth_* fields below are this app's own
    registration with the provider (one client_id/secret per Integration,
    set once by an admin) — not per-install credentials. The authorization_code
    grant requires a redirect URI the provider calls back to; a single one is
    mounted at /app-store/oauth/callback/ for every integration, disambiguated
    via the `state` param (see OAuthPendingConnection).
    """

    class Category(models.TextChoices):
        ECOMMERCE = "ECOMMERCE", "E-commerce"
        ERP = "ERP", "ERP"
        ACCOUNTING = "ACCOUNTING", "Accounting"
        NOTIFICATIONS = "NOTIFICATIONS", "Notifications"
        CRM = "CRM", "CRM"
        FILE = "FILE", "File"
        CUSTOM = "CUSTOM", "Custom"

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.CUSTOM)
    icon = models.CharField(max_length=50, default="bi-puzzle", help_text="A Bootstrap Icons class, e.g. 'bi-shop' — used as a fallback when no logo image is uploaded.")
    icon_image = models.FileField(
        upload_to="integrations/icons/%Y/%m/", null=True, blank=True,
        validators=[FileExtensionValidator(allowed_extensions=ICON_IMAGE_EXTENSIONS), validate_icon_image_size],
        help_text=(
            "Optional uploaded logo (PNG/JPG/GIF/WEBP/SVG, up to "
            f"{ICON_IMAGE_MAX_SIZE_MB}MB); shown on the App Store card instead of "
            "the icon class above when set. A plain FileField rather than an "
            "ImageField — Pillow (which ImageField validates through) isn't a "
            "project dependency, and it can't decode SVG anyway."
        ),
    )

    auth_type = models.CharField(
        max_length=20, choices=Connection.AUTH_TYPE_CHOICES, default=Connection.AUTH_BEARER,
        help_text="Authentication scheme this app expects — determines which fields the install form asks for.",
    )
    default_base_url = models.URLField(blank=True, help_text="Pre-filled base URL for installs of this integration.")
    is_file_based = models.BooleanField(
        default=False,
        help_text="This app has no real API behind it at all — installs skip Base URL/auth entirely, and "
                   "each Connection's entities get their records from an uploaded CSV/XLSX file instead of "
                   "an HTTP call (see schemas.discovery.read_all_records_from_source_file, used by "
                   "jobs/engine.py). Used for the built-in CSV/XLSX apps.",
    )

    # OAuth2-specific — only meaningful when auth_type == OAUTH2.
    oauth_authorize_url = models.URLField(blank=True, help_text="Provider's /authorize endpoint.")
    oauth_token_url = models.URLField(blank=True, help_text="Provider's /token endpoint.")
    oauth_client_id = models.CharField(max_length=255, blank=True)
    oauth_client_secret = models.CharField(max_length=255, blank=True)
    oauth_scope = models.CharField(max_length=255, blank=True)

    setup_url = models.URLField(blank=True, help_text="External docs/setup link shown on the install form, if any.")
    is_featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_featured", "name"]

    def __str__(self):
        return self.name

    @property
    def uses_oauth_redirect(self) -> bool:
        return self.auth_type == Connection.AUTH_OAUTH2


class OAuthPendingConnection(models.Model):
    """
    Tracks an in-progress OAuth2 authorization_code handshake: created the
    moment a user submits the install form for an OAuth integration, and
    consumed when the provider redirects back to the app's single mounted
    callback URL with a matching `state`.
    """

    integration = models.ForeignKey(Integration, on_delete=models.CASCADE, related_name="pending_oauth_connections")
    state = models.CharField(max_length=64, unique=True, default=generate_oauth_state, editable=False)
    connection_name = models.CharField(max_length=120, help_text="Name for the Connection this install will create.")
    reconnect_connection = models.ForeignKey(
        Connection, on_delete=models.CASCADE, null=True, blank=True, related_name="oauth_reconnect_attempts",
        help_text="Set when this handshake is re-authorizing an existing App Store install rather than "
                   "creating a new one — see integrations.views.reconnect_connection(). Re-using the same "
                   "shared callback URL here (instead of a per-connection one) is what lets a provider "
                   "registration with a single redirect URI work for both installing and reconnecting.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pending OAuth connect: {self.integration.name} ({self.state[:8]}...)"


class InstalledIntegration(models.Model):
    """One "install" of an Integration — links it to the Connection actually
    doing the work."""

    integration = models.ForeignKey(Integration, on_delete=models.CASCADE, related_name="installs")
    connection = models.OneToOneField(Connection, on_delete=models.CASCADE, related_name="integration_install")
    installed_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-installed_at"]

    def __str__(self):
        return f"{self.integration.name} ({'active' if self.is_active else 'disabled'})"
