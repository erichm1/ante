from django.contrib import admin

from .models import Attachment


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display  = ("name", "incident", "ticket", "uploaded_by", "size", "created_at")
    list_filter   = ("created_at",)
    search_fields = ("name",)
    readonly_fields = ("size", "created_at")
