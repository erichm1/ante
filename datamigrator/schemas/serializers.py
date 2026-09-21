from rest_framework import serializers

from .models import Entity, Field


class FieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = Field
        fields = ["id", "entity", "name", "field_type", "required", "sample_value"]


class EntitySerializer(serializers.ModelSerializer):
    fields = FieldSerializer(many=True, read_only=True)
    connection_name = serializers.CharField(source="connection.name", read_only=True)
    # Lets the template forms offer only file-backed entities as an import source.
    has_file = serializers.SerializerMethodField()

    class Meta:
        model = Entity
        fields = [
            "id", "connection", "connection_name", "name", "endpoint_path",
            "source", "canvas_x", "canvas_y", "created_at", "fields", "has_file",
        ]

    def get_has_file(self, obj):
        return bool(obj.source_file)
