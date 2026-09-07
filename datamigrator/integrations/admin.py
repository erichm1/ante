from django.contrib import admin
from django.urls import reverse

from .models import Integration, InstalledIntegration, OAuthPendingConnection


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "auth_type", "is_featured", "is_active")
    list_filter = ("category", "auth_type", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("oauth_redirect_url_display",)
    fieldsets = (
        (None, {"fields": ("name", "slug", "description", "category", "icon", "icon_image")}),
        ("Authentication", {"fields": ("auth_type", "default_base_url")}),
        ("OAuth2 (only used when auth_type is OAuth2)", {
            "fields": (
                "oauth_redirect_url_display", "oauth_authorize_url", "oauth_token_url",
                "oauth_client_id", "oauth_client_secret", "oauth_scope",
            ),
        }),
        ("Catalog listing", {"fields": ("setup_url", "is_featured", "is_active")}),
    )

    def get_form(self, request, obj=None, **kwargs):
        # ModelAdmin display methods only receive `obj`, not `request` — stash
        # it here so oauth_redirect_url_display() can build an absolute URL.
        # Safe for this single-worker dev scaffold; a multi-threaded prod
        # deployment sharing one ModelAdmin instance across requests would
        # need the request passed through some other way.
        self._request = request
        return super().get_form(request, obj, **kwargs)

    @admin.display(description="OAuth2 redirect URL")
    def oauth_redirect_url_display(self, obj):
        path = reverse("integrations:oauth_callback")
        request = getattr(self, "_request", None)
        url = request.build_absolute_uri(path) if request else path
        return (
            f"{url} — register this as the redirect/callback URI in the "
            "provider's OAuth app settings. One URL is shared by every "
            "OAuth2 integration (the `state` param tells the callback which "
            "one and which pending install it belongs to)."
        )


@admin.register(InstalledIntegration)
class InstalledIntegrationAdmin(admin.ModelAdmin):
    list_display = ("integration", "connection", "is_active", "installed_at")
    list_filter = ("is_active",)
    readonly_fields = ("installed_at",)


@admin.register(OAuthPendingConnection)
class OAuthPendingConnectionAdmin(admin.ModelAdmin):
    list_display = ("integration", "connection_name", "state", "created_at")
    readonly_fields = ("state", "created_at")
