from django.contrib import admin

from .models import Report, ReportSection


class ReportSectionInline(admin.TabularInline):
    model = ReportSection
    extra = 0


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    inlines = [ReportSectionInline]
