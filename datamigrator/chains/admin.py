from django.contrib import admin

from .models import CallChain, CallChainRun, CallChainStep, CallChainStepResult


class CallChainStepInline(admin.TabularInline):
    model = CallChainStep
    extra = 0


@admin.register(CallChain)
class CallChainAdmin(admin.ModelAdmin):
    list_display = ("name", "connection", "created_at")
    list_filter = ("connection",)
    inlines = [CallChainStepInline]


class CallChainStepResultInline(admin.TabularInline):
    model = CallChainStepResult
    extra = 0
    readonly_fields = ("order", "name", "method", "resolved_path", "resolved_body", "status_code", "response_json", "error")
    can_delete = False


@admin.register(CallChainRun)
class CallChainRunAdmin(admin.ModelAdmin):
    list_display = ("chain", "status", "started_at", "finished_at", "result_file")
    list_filter = ("status", "chain")
    readonly_fields = ("started_at", "finished_at", "result_file")
    inlines = [CallChainStepResultInline]
