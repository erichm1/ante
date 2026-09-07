from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from rest_framework import routers

from connections import views as connection_views
from schemas import views as schema_views
from mappings import views as mapping_views
from jobs import views as job_views
from plans import views as plan_views
from chains import views as chain_views

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

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path("", RedirectView.as_view(url="/home/", permanent=False)),
    path("accounts/", include("accounts.urls")),
    path("home/", include("home.urls")),
    path("connections/", include("connections.urls")),
    path("app-store/", include("integrations.urls")),
    path("", include("schemas.urls")),
    path("mappings/", include("mappings.urls")),
    path("jobs/", include("jobs.urls")),
    path("plans/", include("plans.urls")),
    path("chains/", include("chains.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
