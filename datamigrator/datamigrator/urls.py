from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from rest_framework import routers

from connections import views as connection_views
from schemas import views as schema_views
from mappings import views as mapping_views
from jobs import views as job_views
from plans import views as plan_views
from chains import views as chain_views
from reports import views as report_views
from incidents import views as incident_views
from tickets import views as ticket_views
from attachments import views as attachment_views
from home import views as home_views
from notifications import api as notification_api
from accounts import api as account_api

router = routers.DefaultRouter()
router.register(r"connections", connection_views.ConnectionViewSet, basename="connection")
router.register(r"token-refresh-jobs", connection_views.TokenRefreshJobViewSet, basename="token-refresh-job")
router.register(r"entities", schema_views.EntityViewSet, basename="entity")
router.register(r"fields", schema_views.FieldViewSet, basename="field")
router.register(r"mappings", mapping_views.MappingViewSet, basename="mapping")
router.register(r"entity-mappings", mapping_views.EntityMappingViewSet, basename="entity-mapping")
router.register(r"field-mappings", mapping_views.FieldMappingViewSet, basename="field-mapping")
router.register(r"runs", job_views.MigrationRunViewSet, basename="run")
router.register(r"plans", plan_views.MigrationPlanViewSet, basename="plan")
router.register(r"plan-steps", plan_views.PlanStepViewSet, basename="plan-step")
router.register(r"chains", chain_views.CallChainViewSet, basename="chain")
router.register(r"chain-steps", chain_views.CallChainStepViewSet, basename="chain-step")
router.register(r"reports", report_views.ReportViewSet, basename="report")
router.register(r"report-sections", report_views.ReportSectionViewSet, basename="report-section")
router.register(r"incidents", incident_views.IncidentViewSet, basename="incident")
router.register(r"incident-notes", incident_views.IncidentNoteViewSet, basename="incident-note")
router.register(r"tickets", ticket_views.TicketViewSet, basename="ticket")
router.register(r"ticket-notes", ticket_views.TicketNoteViewSet, basename="ticket-note")
router.register(r"attachments", attachment_views.AttachmentViewSet, basename="attachment")
router.register(r"notifications", notification_api.NotificationViewSet, basename="notification")
router.register(r"admin-users", account_api.UserAdminViewSet, basename="admin-user")
router.register(r"admin-groups", account_api.AccessGroupViewSet, basename="admin-group")
router.register(r"admin-departments", account_api.DepartmentViewSet, basename="admin-department")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/admin-modules/", account_api.ModuleListView.as_view(), name="admin-modules"),
    path("api/", include(router.urls)),
    path("", home_views.landing, name="landing"),
    path("accounts/", include("accounts.urls")),
    path("home/", include("home.urls")),
    path("connections/", include("connections.urls")),
    path("app-store/", include("integrations.urls")),
    path("", include("schemas.urls")),
    path("mappings/", include("mappings.urls")),
    path("jobs/", include("jobs.urls")),
    path("plans/", include("plans.urls")),
    path("chains/", include("chains.urls")),
    path("studio/", include("studio.urls")),
    path("reports/", include("reports.urls")),
    path("incidents/", include("incidents.urls")),
    path("tickets/", include("tickets.urls")),
    path("notifications/", include("notifications.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
