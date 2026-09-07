from django.contrib import admin

from .models import MigrationPlan, PlanStep


class PlanStepInline(admin.TabularInline):
    model = PlanStep
    extra = 0
    readonly_fields = ("run",)


@admin.register(MigrationPlan)
class MigrationPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "scheduled_at", "rate_limit_per_second", "created_at")
    list_filter = ("status",)
    inlines = [PlanStepInline]
