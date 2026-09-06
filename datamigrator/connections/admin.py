from django.contrib import admin

from .models import Connection


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "base_url", "auth_type", "is_active", "is_connected")
    list_filter = ("auth_type", "is_active")
    exclude = ("secrets_encrypted",)
    readonly_fields = ("created_at", "updated_at")
