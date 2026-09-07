from django.shortcuts import get_object_or_404, render
from rest_framework import viewsets

from connections.models import Connection
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

def mapping_list(request):
    mappings = Mapping.objects.select_related("source_connection").prefetch_related("destination_connections")
    connections = Connection.objects.all()
    return render(request, "mappings/list.html", {"mappings": mappings, "connections": connections})


def mapping_raw(request, pk):
    """Plain-table view of the same entity/field mappings the canvas shows
    visually — for scanning a big mapping quickly rather than dragging
    around a board. Shares the exact prefetch shape mapping_canvas uses."""
    mapping = get_object_or_404(Mapping, pk=pk)
    entity_mappings = mapping.entity_mappings.select_related(
        "source_entity", "source_entity__connection", "target_entity", "target_entity__connection",
    ).prefetch_related("field_mappings__source_field", "field_mappings__target_field")
    return render(request, "mappings/raw.html", {
        "mapping": mapping, "entity_mappings": entity_mappings, "write_methods": EntityMapping.METHOD_CHOICES,
    })


def mapping_canvas(request, pk):
    mapping = get_object_or_404(Mapping, pk=pk)
    source_entities = Entity.objects.filter(connection=mapping.source_connection).prefetch_related("fields")
    entity_mappings = mapping.entity_mappings.select_related(
        "source_entity", "source_entity__connection", "target_entity", "target_entity__connection",
    ).prefetch_related("field_mappings")

    # Target entities aren't preloaded by connection anymore (there can be several
    # destination connections) — the canvas renders whatever's already placed from
    # entity_mappings' nested detail, and fetches a connection's entities on demand
    # via /api/entities/?connection=<id> when adding a new pair.
    canvas_data = {
        "mapping": MappingSerializer(mapping).data,
        "source_entities": EntitySerializer(source_entities, many=True).data,
        "entity_mappings": EntityMappingSerializer(entity_mappings, many=True).data,
        "field_types": list(Field.TYPE_CHOICES),
    }
    return render(request, "mappings/canvas.html", {
        "mapping": mapping, "canvas_data": canvas_data, "write_methods": EntityMapping.METHOD_CHOICES,
    })
