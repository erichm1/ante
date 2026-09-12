from django.db import models


class MigrationPlan(models.Model):
    """An ordered bundle of steps staged to run as one "massive migration"
    — build it up (PlanStep by PlanStep) while `status == DRAFT`, then
    Execute (now or scheduled) runs each step sequentially via
    plans/executor.py, one at a time.

    `execution_mode` is chosen once, at creation, and fixed afterward — it
    decides what a step actually IS for the whole plan: MODE_SIMPLE steps
    are Mappings (each becomes a jobs.MigrationRun, today's original
    behavior); MODE_CHAIN steps are chains.CallChain (each becomes a
    chains.CallChainRun instead) — see PlanStep and plans/executor.py."""

    MODE_SIMPLE = "simple"
    MODE_CHAIN = "chain"
    MODE_CHOICES = [
        (MODE_SIMPLE, "Simple execution — sequential mapping runs"),
        (MODE_CHAIN, "Chain mode — sequential chain executions"),
    ]

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
    execution_mode = models.CharField(
        max_length=10, choices=MODE_CHOICES, default=MODE_SIMPLE,
        help_text="Chosen once at creation and fixed afterward — decides whether this plan's steps "
                   "are Mappings (simple) or Chains (chain mode).",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    scheduled_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Set when this plan was told to execute at a future time instead of immediately.",
    )
    rate_limit_per_second = models.FloatField(
        null=True, blank=True,
        help_text="Default requests/second cap applied to every simple-mode step's run — a step can "
                   "override this. Unused in chain mode (chains have no rate limiting of their own).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def is_editable(self) -> bool:
        return self.status == self.STATUS_DRAFT

    @property
    def rate_limit_per_minute(self):
        """rate_limit_per_second, expressed the way it's entered/displayed in
        the UI (requests/minute) — see static/js/rate_limit.js."""
        return round(self.rate_limit_per_second * 60, 2) if self.rate_limit_per_second else None


class PlanStep(models.Model):
    """One step in a plan's sequence — either a Mapping (simple mode) or a
    Chain (chain mode), matching the owning plan's execution_mode; exactly
    one of `mapping`/`chain` is set. `run`/`chain_run` stay null until the
    plan's executor actually starts this step; re-executing a plan
    overwrites them with a fresh MigrationRun/CallChainRun (no partial-
    resume in this scaffold)."""

    plan = models.ForeignKey(MigrationPlan, on_delete=models.CASCADE, related_name="steps")
    mapping = models.ForeignKey(
        "mappings.Mapping", on_delete=models.CASCADE, related_name="+", null=True, blank=True,
    )
    chain = models.ForeignKey(
        "chains.CallChain", on_delete=models.CASCADE, related_name="+", null=True, blank=True,
    )
    order = models.PositiveIntegerField()
    rate_limit_per_second = models.FloatField(
        null=True, blank=True, help_text="Overrides the plan's rate limit for this step only (simple mode only).",
    )
    run = models.ForeignKey(
        "jobs.MigrationRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    chain_run = models.ForeignKey(
        "chains.CallChainRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    class Meta:
        ordering = ["order"]
        unique_together = ("plan", "order")

    def __str__(self):
        target = self.mapping.name if self.mapping_id else self.chain.name
        return f"{self.plan.name} step {self.order}: {target}"

    @property
    def rate_limit_per_minute(self):
        """rate_limit_per_second, expressed the way it's entered/displayed in
        the UI (requests/minute) — see static/js/rate_limit.js."""
        return round(self.rate_limit_per_second * 60, 2) if self.rate_limit_per_second else None
