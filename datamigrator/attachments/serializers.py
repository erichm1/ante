from rest_framework import serializers

from .models import Attachment, MAX_ATTACHMENT_BYTES


class AttachmentSerializer(serializers.ModelSerializer):
    file_url            = serializers.SerializerMethodField()
    uploaded_by_username = serializers.SerializerMethodField()

    class Meta:
        model  = Attachment
        fields = (
            "id", "incident", "ticket", "name", "size",
            "file", "file_url", "uploaded_by_username", "created_at",
        )
        read_only_fields = ("id", "name", "size", "uploaded_by_username", "created_at")
        extra_kwargs = {"file": {"write_only": True}}

    def get_file_url(self, obj):
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return None

    def get_uploaded_by_username(self, obj):
        return obj.uploaded_by.username if obj.uploaded_by_id else None

    def validate_file(self, value):
        if value.size > MAX_ATTACHMENT_BYTES:
            raise serializers.ValidationError(
                f"File too large — maximum is {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB."
            )
        return value

    def validate(self, data):
        if not data.get("incident") and not data.get("ticket"):
            raise serializers.ValidationError("Provide either incident or ticket.")
        return data

    def create(self, validated_data):
        validated_data["uploaded_by"] = self.context["request"].user
        validated_data["name"] = validated_data["file"].name
        validated_data["size"] = validated_data["file"].size
        return super().create(validated_data)
