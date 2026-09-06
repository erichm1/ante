from django.contrib import admin

from .models import EntityMapping, FieldMapping, Mapping


class EntityMappingInline(admin.TabularInline):
    model = EntityMapping
    extra = 0


@admin.register(Mapping)
class MappingAdmin(admin.ModelAdmin):
    list_display = ("name", "source_connection", "created_at")
    filter_horizontal = ("destination_connections",)
    inlines = [EntityMappingInline]


@admin.register(FieldMapping)
class FieldMappingAdmin(admin.ModelAdmin):
    list_display = ("entity_mapping", "source_field", "target_field", "transform")
