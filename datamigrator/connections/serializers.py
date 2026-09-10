from rest_framework import serializers

from .models import Connection, TokenRefreshJob


class ConnectionSerializer(serializers.ModelSerializer):
    is_connected = serializers.BooleanField(read_only=True)

    class Meta:
        model = Connection
        fields = [
            "id", "name", "base_url", "auth_type", "auth_config",
            "use_custom_headers", "custom_headers", "use_custom_params", "custom_params",
            "is_active", "is_connected", "created_at", "updated_at",
        ]


class ConnectionSecretsSerializer(serializers.Serializer):
    """Accepts bearer / basic / JWT secrets. Write-only — never returned."""

    token = serializers.CharField(required=False, allow_blank=True)
    username = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(required=False, allow_blank=True)
    signing_secret = serializers.CharField(required=False, allow_blank=True)


class TokenRefreshJobSerializer(serializers.ModelSerializer):
    connection_name = serializers.CharField(source="connection.name", read_only=True)

    class Meta:
        model = TokenRefreshJob
        fields = [
            "id", "connection", "connection_name", "is_enabled", "interval_minutes",
            "last_run_at", "last_status", "last_error", "next_run_at", "created_at",
        ]
        read_only_fields = ["last_run_at", "last_status", "last_error", "next_run_at", "created_at"]

    def validate_connection(self, connection):
        if connection.auth_type != Connection.AUTH_OAUTH2:
            raise serializers.ValidationError("Only OAuth2 connections can have a refresh job.")
        return connection

    def validate_interval_minutes(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be greater than zero.")
        return value
