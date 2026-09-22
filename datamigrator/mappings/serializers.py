from rest_framework import serializers

from schemas.serializers import EntitySerializer

from .models import EntityMapping, FieldMapping, Mapping


class MappingSerializer(serializers.ModelSerializer):
    source_connection_name = serializers.CharField(source="source_connection.name", read_only=True)
    destination_connections_detail = serializers.SerializerMethodField()
    entity_pairs_count = serializers.SerializerMethodField()
    runs_count = serializers.SerializerMethodField()
    destinations_in_use = serializers.SerializerMethodField()
    draft_count = serializers.SerializerMethodField()

    class Meta:
        model = Mapping
        fields = [
            "id", "name", "description",
            "source_connection", "source_connection_name",
            "destination_connections", "destination_connections_detail",
            "entity_pairs_count", "runs_count", "destinations_in_use", "draft_count",
            "created_at",
        ]

    def get_draft_count(self, obj):
        return FieldMapping.objects.filter(entity_mapping__mapping=obj, status=FieldMapping.STATUS_DRAFT).count()

    def get_destination_connections_detail(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.destination_connections.all()]

    def get_entity_pairs_count(self, obj):
        return len(obj.entity_mappings.all())

    def get_runs_count(self, obj):
        annotated = getattr(obj, "runs_total", None)
        return annotated if annotated is not None else obj.runs.count()

    def get_destinations_in_use(self, obj):
        """Destination connections an entity pair already writes to — they can't be taken off the mapping."""
        return sorted({em.target_entity.connection_id for em in obj.entity_mappings.all()})

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A name is required.")
        return value

    def validate(self, attrs):
        """Keep a mapping consistent with what is already wired inside it: its origin can't change once it has
        entity pairs (their source entities belong to that origin), a destination that a pair writes to can't be
        dropped, and a system can't be both origin and destination."""
        instance = self.instance
        source = attrs.get("source_connection") or (instance.source_connection if instance else None)
        destinations = attrs.get("destination_connections")

        if destinations is not None and source and any(d.pk == source.pk for d in destinations):
            raise serializers.ValidationError({"destination_connections": "A destination can't be the same system as the origin."})
        if instance is not None:
            pairs = list(instance.entity_mappings.select_related("target_entity__connection"))
            if "source_connection" in attrs and attrs["source_connection"].pk != instance.source_connection_id and pairs:
                raise serializers.ValidationError({"source_connection": "The origin can't change once the mapping has entity pairs — remove them first, or duplicate the mapping."})
            if destinations is not None:
                keep = {d.pk for d in destinations}
                blocked = sorted({em.target_entity.connection.name for em in pairs if em.target_entity.connection_id not in keep})
                if blocked:
                    raise serializers.ValidationError({"destination_connections": f"Still used by an entity pair: {', '.join(blocked)}. Remove those pairs first."})
        return attrs


class FieldMappingSerializer(serializers.ModelSerializer):
    source_field_name = serializers.CharField(source="source_field.name", read_only=True)
    target_field_name = serializers.CharField(source="target_field.name", read_only=True)

    class Meta:
        model = FieldMapping
        fields = [
            "id", "entity_mapping", "source_field", "target_field",
            "source_field_name", "target_field_name", "transform_rules", "transform",
            "status", "match_score", "match_reason",
        ]
        read_only_fields = ["match_score", "match_reason"]


class EntityMappingSerializer(serializers.ModelSerializer):
    source_entity_detail = EntitySerializer(source="source_entity", read_only=True)
    target_entity_detail = EntitySerializer(source="target_entity", read_only=True)
    field_mappings = FieldMappingSerializer(many=True, read_only=True)
    draft_count = serializers.SerializerMethodField()

    class Meta:
        model = EntityMapping
        fields = [
            "id", "mapping", "source_entity", "target_entity", "write_method",
            "source_entity_detail", "target_entity_detail", "field_mappings", "draft_count",
        ]

    def get_draft_count(self, obj):
        return sum(1 for fm in obj.field_mappings.all() if fm.status == FieldMapping.STATUS_DRAFT)

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
