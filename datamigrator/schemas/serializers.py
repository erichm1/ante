from rest_framework import serializers

from .models import Entity, Field


class FieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = Field
        fields = ["id", "entity", "name", "field_type", "required", "sample_value"]


class EntitySerializer(serializers.ModelSerializer):
    fields = FieldSerializer(many=True, read_only=True)
    connection_name = serializers.CharField(source="connection.name", read_only=True)

    class Meta:
        model = Entity
        fields = [
            "id", "connection", "connection_name", "name", "endpoint_path",
            "source", "canvas_x", "canvas_y", "created_at", "fields",
        ]
