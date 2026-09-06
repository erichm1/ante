from django.contrib import admin

from .models import Entity, Field


class FieldInline(admin.TabularInline):
    model = Field
    extra = 1


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = ("name", "connection", "source", "endpoint_path")
    list_filter = ("connection", "source")
    inlines = [FieldInline]
