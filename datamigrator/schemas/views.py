import json

import requests as pyrequests
from django.shortcuts import get_object_or_404, render
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from connections.client import ConnectionClient
from connections.models import Connection

from . import discovery
from .models import Entity, Field
from .serializers import EntitySerializer, FieldSerializer


class EntityViewSet(viewsets.ModelViewSet):
    queryset = Entity.objects.all().prefetch_related("fields")
    serializer_class = EntitySerializer
    filterset_fields = ["connection"]

    @action(detail=False, methods=["post"], url_path="discover/sample")
    def discover_sample(self, request):
        connection = get_object_or_404(Connection, pk=request.data["connection_id"])
        client = ConnectionClient(connection)
        try:
            entity = discovery.discover_from_sample(
                connection, request.data["entity_name"], request.data["endpoint_path"], client,
            )
        except Exception as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(EntitySerializer(entity).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="discover/openapi")
    def discover_openapi(self, request):
        connection = get_object_or_404(Connection, pk=request.data["connection_id"])
        spec_url = request.data.get("spec_url")
        spec_file = request.FILES.get("spec_file")
        try:
            if spec_file:
                spec = json.load(spec_file)
            elif spec_url:
                spec = pyrequests.get(spec_url, timeout=30).json()
            else:
                spec = request.data.get("spec")
            entities = discovery.discover_from_openapi(
                connection, spec,
                schema_names=request.data.get("schema_names"),
                endpoint_paths=request.data.get("endpoint_paths"),
            )
        except Exception as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(EntitySerializer(entities, many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="discover/file")
    def discover_file(self, request):
        connection = get_object_or_404(Connection, pk=request.data["connection_id"])
        entity_name = request.data.get("entity_name")
        endpoint_path = request.data.get("endpoint_path", "")
        upload = request.FILES.get("file")
        if not upload:
            return Response({"error": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if upload.name.lower().endswith(".csv"):
                entity = discovery.discover_from_csv(connection, entity_name, endpoint_path, upload)
            elif upload.name.lower().endswith(".xlsx"):
                entity = discovery.discover_from_xlsx(connection, entity_name, endpoint_path, upload)
            else:
                return Response({"error": "Only .csv or .xlsx files are supported."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(EntitySerializer(entity).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], url_path="position")
    def update_position(self, request, pk=None):
        entity = self.get_object()
        entity.canvas_x = request.data.get("canvas_x", entity.canvas_x)
        entity.canvas_y = request.data.get("canvas_y", entity.canvas_y)
        entity.save(update_fields=["canvas_x", "canvas_y"])
        return Response({"status": "saved"})


class FieldViewSet(viewsets.ModelViewSet):
    queryset = Field.objects.all()
    serializer_class = FieldSerializer
    filterset_fields = ["entity"]


def entity_list(request, connection_pk):
    connection = get_object_or_404(Connection, pk=connection_pk)
    entities = connection.entities.prefetch_related("fields")
    return render(request, "schemas/entities.html", {
        "connection": connection, "entities": entities, "field_types": list(Field.TYPE_CHOICES),
    })
