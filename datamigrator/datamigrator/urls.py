from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from rest_framework import routers

from connections import views as connection_views
from schemas import views as schema_views
from mappings import views as mapping_views
from jobs import views as job_views

router = routers.DefaultRouter()
router.register(r"connections", connection_views.ConnectionViewSet, basename="connection")
router.register(r"entities", schema_views.EntityViewSet, basename="entity")
router.register(r"fields", schema_views.FieldViewSet, basename="field")
router.register(r"mappings", mapping_views.MappingViewSet, basename="mapping")
router.register(r"entity-mappings", mapping_views.EntityMappingViewSet, basename="entity-mapping")
router.register(r"field-mappings", mapping_views.FieldMappingViewSet, basename="field-mapping")
router.register(r"runs", job_views.MigrationRunViewSet, basename="run")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path("", RedirectView.as_view(url="/connections/", permanent=False)),
    path("connections/", include("connections.urls")),
    path("", include("schemas.urls")),
    path("mappings/", include("mappings.urls")),
    path("jobs/", include("jobs.urls")),
]
