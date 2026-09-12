from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from rest_framework import viewsets

from connections.models import Connection
from jobs.models import MigrationRun
from jobs.views import _connection_integration, _route_info
from schemas.models import Entity, Field
from schemas.serializers import EntitySerializer

from .models import EntityMapping, FieldMapping, Mapping
from .serializers import EntityMappingSerializer, FieldMappingSerializer, MappingSerializer


class MappingViewSet(viewsets.ModelViewSet):
    queryset = Mapping.objects.select_related("source_connection").prefetch_related("destination_connections")
    serializer_class = MappingSerializer


class EntityMappingViewSet(viewsets.ModelViewSet):
    queryset = EntityMapping.objects.select_related(
        "source_entity", "source_entity__connection", "target_entity", "target_entity__connection",
    ).prefetch_related("field_mappings")
    serializer_class = EntityMappingSerializer
    filterset_fields = ["mapping"]


class FieldMappingViewSet(viewsets.ModelViewSet):
    queryset = FieldMapping.objects.all()
    serializer_class = FieldMappingSerializer
    filterset_fields = ["entity_mapping"]


# ---- Page views -----------------------------------------------------------

PAGE_SIZE_CHOICES = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 10
TABS = ("canvas", "raw", "runs", "connections")


def mapping_list(request):
    mappings = Mapping.objects.select_related("source_connection").prefetch_related("destination_connections")
    connections = Connection.objects.all()
    return render(request, "mappings/list.html", {"mappings": mappings, "connections": connections})


def canvas_list(request):
    """Same underlying Mapping objects as Mappings — its own sidebar entry
    per the user's request, opening straight into a mapping's canvas tab
    instead of its raw table."""
    mappings = Mapping.objects.select_related("source_connection").prefetch_related("destination_connections")
    connections = Connection.objects.all()
    return render(request, "mappings/canvas_list.html", {"mappings": mappings, "connections": connections})


def mapping_detail(request, pk):
    """One workspace per mapping: Canvas / Raw / Runs / Connections as tabs
    (?tab=, same plain-link pattern as jobs:run_list) — replaces the old
    separate mappings:raw / mappings:canvas pages, and folds in this
    mapping's own run history and connection summaries so there's no need
    to jump out to /jobs/ or /connections/ just to check on it."""
    mapping = get_object_or_404(
        Mapping.objects.select_related("source_connection").prefetch_related("destination_connections"), pk=pk,
    )
    tab = request.GET.get("tab", "canvas")
    if tab not in TABS:
        tab = "canvas"

    context = {
        "mapping": mapping, "tab": tab, "write_methods": EntityMapping.METHOD_CHOICES,
        "route": _route_info(mapping),
    }

    if tab == "canvas":
        source_entities = Entity.objects.filter(connection=mapping.source_connection).prefetch_related("fields")
        entity_mappings = mapping.entity_mappings.select_related(
            "source_entity", "source_entity__connection", "target_entity", "target_entity__connection",
        ).prefetch_related("field_mappings")
        context["canvas_data"] = {
            "mapping": MappingSerializer(mapping).data,
            "source_entities": EntitySerializer(source_entities, many=True).data,
            "entity_mappings": EntityMappingSerializer(entity_mappings, many=True).data,
            "field_types": list(Field.TYPE_CHOICES),
        }

    elif tab == "raw":
        context["entity_mappings"] = mapping.entity_mappings.select_related(
            "source_entity", "source_entity__connection", "target_entity", "target_entity__connection",
        ).prefetch_related("field_mappings__source_field", "field_mappings__target_field")

    elif tab == "runs":
        runs = MigrationRun.objects.filter(mapping=mapping).order_by("-started_at")
        try:
            page_size = int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))
        except ValueError:
            page_size = DEFAULT_PAGE_SIZE
        if page_size not in PAGE_SIZE_CHOICES:
            page_size = DEFAULT_PAGE_SIZE
        context["page_obj"] = Paginator(runs, page_size).get_page(request.GET.get("page"))
        context["page_size"] = page_size
        context["page_size_choices"] = PAGE_SIZE_CHOICES

    elif tab == "connections":
        connections = [mapping.source_connection] + list(mapping.destination_connections.all())
        context["connection_roles"] = [
            {
                "connection": c,
                "integration": _connection_integration(c),
                "role": "Source" if c.id == mapping.source_connection_id else "Destination",
                "entity_count": Entity.objects.filter(connection=c).count(),
            }
            for c in connections
        ]

    return render(request, "mappings/detail.html", context)
