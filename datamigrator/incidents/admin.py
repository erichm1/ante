from django.contrib import admin

from .models import Incident, IncidentNote, IncidentRule, PostMortem


class IncidentNoteInline(admin.TabularInline):
    model = IncidentNote
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display  = ("title", "severity", "status", "connection", "created_at")
    list_filter   = ("status", "severity")
    search_fields = ("title", "description")
    inlines       = [IncidentNoteInline]
    readonly_fields = ("sla_response_deadline", "sla_resolution_deadline", "created_at")


@admin.register(IncidentNote)
class IncidentNoteAdmin(admin.ModelAdmin):
    list_display = ("incident", "created_at")
    readonly_fields = ("created_at",)


@admin.register(PostMortem)
class PostMortemAdmin(admin.ModelAdmin):
    list_display = ("incident", "written_by", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


@admin.register(IncidentRule)
class IncidentRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "trigger_type", "threshold", "severity", "enabled", "user", "last_triggered_at")
    list_filter  = ("enabled", "trigger_type", "severity")
