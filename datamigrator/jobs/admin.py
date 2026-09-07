from django.contrib import admin

from .models import MigrationLog, MigrationRun, RunStepStatus


class MigrationLogInline(admin.TabularInline):
    model = MigrationLog
    extra = 0
    readonly_fields = ("level", "message", "created_at")


class RunStepStatusInline(admin.TabularInline):
    model = RunStepStatus
    extra = 0
    readonly_fields = ("entity_mapping", "status", "records_read", "records_written", "records_failed", "started_at", "finished_at", "error_message")


@admin.register(MigrationRun)
class MigrationRunAdmin(admin.ModelAdmin):
    list_display = (
        "id", "mapping", "status", "records_read", "records_written",
        "records_failed", "requests_made", "rate_limit_per_second", "scheduled_at", "started_at",
    )
    list_filter = ("status",)
    inlines = [RunStepStatusInline, MigrationLogInline]
