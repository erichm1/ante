from rest_framework import serializers

from schemas.serializers import EntitySerializer

from .models import EntityMapping, FieldMapping, Mapping


class MappingSerializer(serializers.ModelSerializer):
    source_connection_name = serializers.CharField(source="source_connection.name", read_only=True)
    destination_connections_detail = serializers.SerializerMethodField()

    class Meta:
        model = Mapping
        fields = [
            "id", "name", "description",
            "source_connection", "source_connection_name",
            "destination_connections", "destination_connections_detail",
            "created_at",
        ]

    def get_destination_connections_detail(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.destination_connections.all()]


class FieldMappingSerializer(serializers.ModelSerializer):
    source_field_name = serializers.CharField(source="source_field.name", read_only=True)
    target_field_name = serializers.CharField(source="target_field.name", read_only=True)

    class Meta:
        model = FieldMapping
        fields = [
            "id", "entity_mapping", "source_field", "target_field",
            "source_field_name", "target_field_name", "transform_rules", "transform",
        ]


class EntityMappingSerializer(serializers.ModelSerializer):
    source_entity_detail = EntitySerializer(source="source_entity", read_only=True)
    target_entity_detail = EntitySerializer(source="target_entity", read_only=True)
    field_mappings = FieldMappingSerializer(many=True, read_only=True)

    class Meta:
        model = EntityMapping
        fields = [
            "id", "mapping", "source_entity", "target_entity", "write_method",
            "source_entity_detail", "target_entity_detail", "field_mappings",
        ]

    def validate(self, attrs):
        mapping = attrs.get("mapping") or getattr(self.instance, "mapping", None)
        source_entity = attrs.get("source_entity") or getattr(self.instance, "source_entity", None)
        target_entity = attrs.get("target_entity") or getattr(self.instance, "target_entity", None)

        if mapping and source_entity and source_entity.connection_id != mapping.source_connection_id:
            raise serializers.ValidationError({
                "source_entity": "Must belong to this mapping's origin connection."
            })
        if mapping and target_entity and not mapping.destination_connections.filter(
            id=target_entity.connection_id
        ).exists():
            raise serializers.ValidationError({
                "target_entity": "Must belong to one of this mapping's destination connections."
            })
        return attrs
