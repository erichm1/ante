from rest_framework import serializers

from .models import MigrationLog, MigrationRun


class MigrationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = MigrationLog
        fields = ["id", "level", "message", "created_at"]


class MigrationRunSerializer(serializers.ModelSerializer):
    logs = MigrationLogSerializer(many=True, read_only=True)
    mapping_name = serializers.CharField(source="mapping.name", read_only=True)

    class Meta:
        model = MigrationRun
        fields = [
            "id", "mapping", "mapping_name", "status", "records_read", "records_written",
            "records_failed", "requests_made", "started_at", "finished_at", "logs",
        ]
