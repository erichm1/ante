from django.contrib import admin

from .models import ApiCallLog, Connection, TokenRefreshJob


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "base_url", "auth_type", "is_active", "is_connected")
    list_filter = ("auth_type", "is_active")
    exclude = ("secrets_encrypted",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(TokenRefreshJob)
class TokenRefreshJobAdmin(admin.ModelAdmin):
    list_display = ("connection", "is_enabled", "interval_minutes", "last_status", "last_run_at", "next_run_at")
    list_filter = ("is_enabled", "last_status")
    readonly_fields = ("last_run_at", "last_status", "last_error", "next_run_at", "created_at")


@admin.register(ApiCallLog)
class ApiCallLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "method", "url", "status_code", "connection", "run", "duration_ms")
    list_filter = ("method", "status_code", "connection")
    search_fields = ("url", "request_body", "response_body", "error")
    readonly_fields = [f.name for f in ApiCallLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
