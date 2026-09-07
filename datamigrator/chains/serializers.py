from rest_framework import serializers

from .models import CallChain, CallChainRun, CallChainStep, CallChainStepResult


class CallChainStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = CallChainStep
        fields = ["id", "chain", "name", "order", "method", "path", "body", "captures"]
        read_only_fields = ["order", "chain"]  # order assigned by add_step; chain never reassigned via PATCH


class CallChainStepResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = CallChainStepResult
        fields = [
            "id", "order", "name", "method", "resolved_path", "resolved_body",
            "status_code", "response_json", "captured_variables", "error",
        ]


class CallChainRunSerializer(serializers.ModelSerializer):
    step_results = CallChainStepResultSerializer(many=True, read_only=True)
    result_file_url = serializers.SerializerMethodField()

    class Meta:
        model = CallChainRun
        fields = ["id", "chain", "status", "started_at", "finished_at", "result_file_url", "step_results"]

    def get_result_file_url(self, obj):
        request = self.context.get("request")
        if not obj.result_file:
            return None
        return request.build_absolute_uri(obj.result_file.url) if request else obj.result_file.url


class CallChainSerializer(serializers.ModelSerializer):
    steps = CallChainStepSerializer(many=True, read_only=True)
    connection_name = serializers.CharField(source="connection.name", read_only=True)

    class Meta:
        model = CallChain
        fields = ["id", "name", "description", "connection", "connection_name", "created_at", "steps"]
