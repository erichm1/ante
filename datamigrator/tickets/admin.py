from django.contrib import admin

from .models import Ticket, TicketNote


class TicketNoteInline(admin.TabularInline):
    model = TicketNote
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display  = ("title", "priority", "status", "created_by", "incident", "created_at")
    list_filter   = ("status", "priority")
    search_fields = ("title", "description")
    inlines       = [TicketNoteInline]


@admin.register(TicketNote)
class TicketNoteAdmin(admin.ModelAdmin):
    list_display = ("ticket", "created_at")
    readonly_fields = ("created_at",)
