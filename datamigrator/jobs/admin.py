from django.contrib import admin

from .models import MigrationLog, MigrationRun


class MigrationLogInline(admin.TabularInline):
    model = MigrationLog
    extra = 0
    readonly_fields = ("level", "message", "created_at")


@admin.register(MigrationRun)
class MigrationRunAdmin(admin.ModelAdmin):
    list_display = (
        "id", "mapping", "status", "records_read", "records_written",
        "records_failed", "requests_made", "started_at",
    )
    list_filter = ("status",)
    inlines = [MigrationLogInline]
