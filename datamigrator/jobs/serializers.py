from rest_framework import serializers

from .models import MigrationLog, MigrationRun, RunStepStatus


class MigrationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = MigrationLog
        fields = ["id", "level", "message", "created_at"]


class RunStepStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunStepStatus
        fields = [
            "id", "entity_mapping", "status", "records_read", "records_written",
            "records_failed", "started_at", "finished_at", "error_message",
        ]


class MigrationRunSerializer(serializers.ModelSerializer):
    logs = MigrationLogSerializer(many=True, read_only=True)
    step_statuses = RunStepStatusSerializer(many=True, read_only=True)
    mapping_name = serializers.CharField(source="mapping.name", read_only=True)

    class Meta:
        model = MigrationRun
        fields = [
            "id", "mapping", "mapping_name", "status", "records_read", "records_written",
            "records_failed", "requests_made", "rate_limit_per_second", "scheduled_at",
            "started_at", "finished_at", "input_file", "logs", "step_statuses",
        ]
