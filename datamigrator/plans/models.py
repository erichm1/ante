from django.db import models


class MigrationPlan(models.Model):
    """An ordered bundle of Mappings staged to run as one "massive migration"
    — build it up (PlanStep by PlanStep) while `status == DRAFT`, then
    Execute (now or scheduled) runs each step's mapping sequentially via
    plans/executor.py, one MigrationRun at a time."""

    STATUS_DRAFT = "draft"
    STATUS_SCHEDULED = "scheduled"
    STATUS_EXECUTING = "executing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_EXECUTING, "Executing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    scheduled_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Set when this plan was told to execute at a future time instead of immediately.",
    )
    rate_limit_per_second = models.FloatField(
        null=True, blank=True,
        help_text="Default requests/second cap applied to every step's run — a step can override this.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def is_editable(self) -> bool:
        return self.status == self.STATUS_DRAFT


class PlanStep(models.Model):
    """One mapping in a plan's sequence. `run` is null until the plan's
    executor actually starts this step; re-executing a plan overwrites it
    with a fresh MigrationRun (no partial-resume in this scaffold)."""

    plan = models.ForeignKey(MigrationPlan, on_delete=models.CASCADE, related_name="steps")
    mapping = models.ForeignKey("mappings.Mapping", on_delete=models.CASCADE, related_name="+")
    order = models.PositiveIntegerField()
    rate_limit_per_second = models.FloatField(
        null=True, blank=True, help_text="Overrides the plan's rate limit for this step only.",
    )
    run = models.ForeignKey(
        "jobs.MigrationRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    class Meta:
        ordering = ["order"]
        unique_together = ("plan", "order")

    def __str__(self):
        return f"{self.plan.name} step {self.order}: {self.mapping.name}"
