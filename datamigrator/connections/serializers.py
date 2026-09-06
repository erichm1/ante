from rest_framework import serializers

from .models import Connection


class ConnectionSerializer(serializers.ModelSerializer):
    is_connected = serializers.BooleanField(read_only=True)

    class Meta:
        model = Connection
        fields = [
            "id", "name", "base_url", "auth_type", "auth_config",
            "is_active", "is_connected", "created_at", "updated_at",
        ]


class ConnectionSecretsSerializer(serializers.Serializer):
    """Accepts API key / basic-auth secrets. Write-only — never returned."""

    api_key = serializers.CharField(required=False, allow_blank=True)
    username = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(required=False, allow_blank=True)
