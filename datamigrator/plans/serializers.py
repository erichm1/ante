from rest_framework import serializers

from jobs.models import MigrationRun

from .models import MigrationPlan, PlanStep


def validate_positive_rate_limit(value):
    # jobs/engine.py's throttle() does 1.0 / rate_limit and time.sleep(...) with
    # it — zero or negative would divide-by-zero or crash time.sleep() outright.
    if value is not None and value <= 0:
        raise serializers.ValidationError("Must be greater than zero.")
    return value


class StepRunSerializer(serializers.ModelSerializer):
    """Trimmed run info for a step — the full MigrationRun (logs, step
    statuses of its own) is one click away via the run's own detail/pipeline
    pages; the plan detail page only needs enough to render a status chip
    and counts."""

    class Meta:
        model = MigrationRun
        fields = ["id", "status", "records_read", "records_written", "records_failed", "requests_made"]


class PlanStepSerializer(serializers.ModelSerializer):
    """`add_step`/`move` in plans/views.py build/reorder steps directly
    (not through this serializer's create/update), so the only field a plain
    PATCH can change is rate_limit_per_second — mapping/order/plan/run stay
    read-only here to keep those dedicated actions the single source of
    truth for structure."""

    mapping_name = serializers.CharField(source="mapping.name", read_only=True)
    run = StepRunSerializer(read_only=True)
    rate_limit_per_second = serializers.FloatField(
        required=False, allow_null=True, validators=[validate_positive_rate_limit],
    )

    class Meta:
        model = PlanStep
        fields = ["id", "plan", "mapping", "mapping_name", "order", "rate_limit_per_second", "run"]
        read_only_fields = ["plan", "mapping", "order", "run"]


class MigrationPlanSerializer(serializers.ModelSerializer):
    """status/scheduled_at only ever change through the `execute` action in
    plans/views.py (which also starts/schedules the executor) — a plain
    PATCH here is for editing the plan's own fields (name, description,
    default rate limit) before you execute it, not for faking a state
    transition."""

    steps = PlanStepSerializer(many=True, read_only=True)
    rate_limit_per_second = serializers.FloatField(
        required=False, allow_null=True, validators=[validate_positive_rate_limit],
    )

    class Meta:
        model = MigrationPlan
        fields = [
            "id", "name", "description", "status", "scheduled_at",
            "rate_limit_per_second", "created_at", "steps",
        ]
        read_only_fields = ["status", "scheduled_at"]
