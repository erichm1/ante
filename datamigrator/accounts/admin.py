from django.contrib import admin

from .models import AccessGroup, Department, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "department", "company_name", "company_document")
    list_filter = ("department", "groups")
    filter_horizontal = ("groups",)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "description")


@admin.register(AccessGroup)
class AccessGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "is_default", "modules")
