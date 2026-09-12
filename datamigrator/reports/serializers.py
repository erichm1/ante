from rest_framework import serializers

from schemas.models import Field

from .models import Report, ReportSection


class ReportSectionSerializer(serializers.ModelSerializer):
    entity_name = serializers.CharField(source="entity.name", read_only=True)
    # method_name set explicitly — "get_field_names" collides with
    # ModelSerializer's own internal get_field_names(declared_fields, info).
    field_names = serializers.SerializerMethodField(method_name="_resolve_field_names")

    class Meta:
        model = ReportSection
        fields = ["id", "report", "entity", "entity_name", "order", "field_ids", "field_names"]
        read_only_fields = ["order", "report"]  # order assigned by add_section; report never reassigned via PATCH

    def _resolve_field_names(self, obj):
        fields_by_id = {f.pk: f.name for f in Field.objects.filter(pk__in=obj.field_ids)}
        return [fields_by_id[fid] for fid in obj.field_ids if fid in fields_by_id]

    def validate(self, attrs):
        entity = attrs.get("entity") or (self.instance.entity if self.instance else None)
        field_ids = attrs.get("field_ids")
        if field_ids is not None and entity is not None:
            valid_ids = set(entity.fields.values_list("pk", flat=True))
            unknown = [fid for fid in field_ids if fid not in valid_ids]
            if unknown:
                raise serializers.ValidationError({"field_ids": f"These field ids don't belong to {entity.name}: {unknown}."})
        return attrs


class ReportSerializer(serializers.ModelSerializer):
    sections = ReportSectionSerializer(many=True, read_only=True)

    class Meta:
        model = Report
        fields = ["id", "name", "description", "created_at", "sections"]
