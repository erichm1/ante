from rest_framework import serializers

from .models import Ticket, TicketNote


class TicketNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model  = TicketNote
        fields = ("id", "ticket", "body", "created_at")
        read_only_fields = ("id", "ticket", "created_at")


class TicketSerializer(serializers.ModelSerializer):
    incident_title        = serializers.SerializerMethodField()
    created_by_username   = serializers.SerializerMethodField()
    sla_response_status   = serializers.SerializerMethodField()
    sla_resolution_status = serializers.SerializerMethodField()

    class Meta:
        model  = Ticket
        fields = (
            "id", "title", "description", "priority", "status",
            "incident", "incident_title",
            "created_by", "created_by_username",
            "created_at", "resolved_at", "acknowledged_at",
            "sla_response_deadline", "sla_resolution_deadline",
            "sla_response_status", "sla_resolution_status",
        )
        read_only_fields = ("id", "created_by", "created_at", "sla_response_deadline", "sla_resolution_deadline")

    def get_incident_title(self, obj):
        return obj.incident.title if obj.incident_id else None

    def get_created_by_username(self, obj):
        return obj.created_by.username if obj.created_by_id else None

    def get_sla_response_status(self, obj):
        return obj.sla_response_status

    def get_sla_resolution_status(self, obj):
        return obj.sla_resolution_status

    def create(self, validated_data):
        validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        from django.utils import timezone
        old_status = instance.status
        new_status = validated_data.get("status", old_status)
        # Acknowledge when status first moves to in_progress
        if old_status == Ticket.STATUS_OPEN and new_status == Ticket.STATUS_IN_PROGRESS:
            if not instance.acknowledged_at:
                instance.acknowledged_at = timezone.now()
        return super().update(instance, validated_data)
