from rest_framework import serializers

from .models import Incident, IncidentNote, IncidentRule, PostMortem, SERVICE_CHOICES


class IncidentNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model  = IncidentNote
        fields = ("id", "incident", "body", "created_at")
        read_only_fields = ("id", "incident", "created_at")


class IncidentSerializer(serializers.ModelSerializer):
    connection_name     = serializers.SerializerMethodField()
    sla_response_status = serializers.SerializerMethodField()
    sla_resolution_status = serializers.SerializerMethodField()

    class Meta:
        model  = Incident
        fields = (
            "id", "title", "description", "severity", "status",
            "connection", "connection_name",
            "affected_services",
            "created_at", "resolved_at", "acknowledged_at",
            "sla_response_deadline", "sla_resolution_deadline",
            "sla_response_status", "sla_resolution_status",
        )
        read_only_fields = ("id", "created_at", "sla_response_deadline", "sla_resolution_deadline")

    def get_connection_name(self, obj):
        return obj.connection.name if obj.connection_id else None

    def get_sla_response_status(self, obj):
        return obj.sla_response_status

    def get_sla_resolution_status(self, obj):
        return obj.sla_resolution_status

    def update(self, instance, validated_data):
        from django.utils import timezone
        old_status = instance.status
        new_status = validated_data.get("status", old_status)
        now = timezone.now()
        if old_status == Incident.STATUS_OPEN and new_status != Incident.STATUS_OPEN:
            if not instance.acknowledged_at:
                instance.acknowledged_at = now
        if new_status == Incident.STATUS_RESOLVED and old_status != Incident.STATUS_RESOLVED:
            if not instance.resolved_at:
                instance.resolved_at = now
        elif new_status != Incident.STATUS_RESOLVED and old_status == Incident.STATUS_RESOLVED:
            instance.resolved_at = None
        return super().update(instance, validated_data)


class IncidentRuleSerializer(serializers.ModelSerializer):
    trigger_type_display = serializers.SerializerMethodField()
    connection_name      = serializers.SerializerMethodField()

    class Meta:
        model  = IncidentRule
        fields = (
            "id", "name", "trigger_type", "trigger_type_display",
            "threshold", "window_hours",
            "connection", "connection_name",
            "severity", "auto_title", "enabled",
            "created_at", "last_triggered_at", "last_checked_at",
        )
        read_only_fields = ("id", "created_at", "last_triggered_at", "last_checked_at")

    def get_trigger_type_display(self, obj):
        return obj.get_trigger_type_display()

    def get_connection_name(self, obj):
        return obj.connection.name if obj.connection_id else None

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class PostMortemSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PostMortem
        fields = (
            "id", "incident", "summary", "timeline", "impact",
            "root_cause", "contributing_factors",
            "what_went_well", "what_went_wrong",
            "action_items", "lessons_learned",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
