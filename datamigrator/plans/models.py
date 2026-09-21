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
    chains.CallChainRun instead); MODE_MIXED (what the Studio workspace
    creates) allows both kinds in one sequence — see PlanStep and
    plans/executor.py, which dispatches on each step's own mapping/chain."""

    MODE_SIMPLE = "simple"
    MODE_CHAIN = "chain"
    MODE_MIXED = "mixed"
    MODE_CHOICES = [
        (MODE_SIMPLE, "Simple execution — sequential mapping runs"),
        (MODE_CHAIN, "Chain mode — sequential chain executions"),
        (MODE_MIXED, "Mixed — mappings and chains in one sequence"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_SCHEDULED = "scheduled"
    STATUS_EXECUTING = "executing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_EXECUTING, "Executing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    execution_mode = models.CharField(
        max_length=10, choices=MODE_CHOICES, default=MODE_SIMPLE,
        help_text="Chosen once at creation and fixed afterward — decides whether this plan's steps "
                   "are Mappings (simple), Chains (chain mode), or either, freely mixed (mixed).",
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
    cancel_requested = models.BooleanField(
        default=False, help_text="Set by the Kill button; plans/executor.py stops before the next step (and mid-wait).",
    )
    inline_chain = models.ForeignKey(
        "chains.CallChain", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Hidden chain holding this plan's function steps (request, find, check, file…), so they "
                   "can pass data to one another during a run.",
    )
    inline_run = models.ForeignKey(
        "chains.CallChainRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="The single chain run all of the plan's function steps recorded their results on, this execution.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def is_editable(self) -> bool:
        """Steps can be rearranged whenever the plan isn't in flight — so a finished (or failed)
        run can be tweaked and executed again from the Studio canvas. Executing/scheduled plans
        stay locked: the executor is (or is about to be) walking those very steps."""
        return self.status in (self.STATUS_DRAFT, self.STATUS_COMPLETED, self.STATUS_FAILED, self.STATUS_CANCELLED)

    @property
    def rate_limit_per_minute(self):
        """rate_limit_per_second, expressed the way it's entered/displayed in
        the UI (requests/minute) — see static/js/rate_limit.js."""
        return round(self.rate_limit_per_second * 60, 2) if self.rate_limit_per_second else None


class PlanStep(models.Model):
    """One step in a plan's sequence. It is exactly one of:
      - a Mapping (`mapping` set) — optionally only some of its entity pairs (`entity_mapping_ids`),
      - a Chain (`chain` set),
      - a function (`inline_step` set) — one step of the plan's shared inline chain, or
      - a Wait (`wait_seconds` set, no mapping/chain) — a pause before the next step (mixed plans only).
    `run`/`chain_run`/`started_at`/`finished_at` stay empty until the plan's executor reaches the
    step; the executor clears them all when a plan starts, so a re-execution never shows the last
    run's results as if they were this one's."""

    plan = models.ForeignKey(MigrationPlan, on_delete=models.CASCADE, related_name="steps")
    mapping = models.ForeignKey(
        "mappings.Mapping", on_delete=models.CASCADE, related_name="+", null=True, blank=True,
    )
    chain = models.ForeignKey(
        "chains.CallChain", on_delete=models.CASCADE, related_name="+", null=True, blank=True,
    )
    inline_step = models.ForeignKey(
        "chains.CallChainStep", on_delete=models.CASCADE, related_name="+", null=True, blank=True,
        help_text="A function step (request, find in list, check header, next page, file preview…) that "
                   "lives in the plan's hidden inline chain.",
    )
    order = models.PositiveIntegerField()
    wait_seconds = models.FloatField(
        null=True, blank=True,
        help_text="Makes this a pause step: no mapping or chain, just wait this long before the next step.",
    )
    entity_mapping_ids = models.JSONField(
        default=list, blank=True,
        help_text="Mapping steps only: run just these entity pairs (ids of the mapping's EntityMappings). "
                   "Empty = the whole mapping.",
    )
    started_at = models.DateTimeField(null=True, blank=True, help_text="Set for a wait step while it pauses.")
    finished_at = models.DateTimeField(null=True, blank=True, help_text="Set for a wait step once it has waited.")
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

    @property
    def kind(self) -> str:
        return ("wait" if self.wait_seconds is not None else "function" if self.inline_step_id
                else "chain" if self.chain_id else "mapping")

    def __str__(self):
        target = (f"wait {self.wait_seconds}s" if self.kind == "wait" else self.inline_step.name if self.kind == "function"
                  else self.chain.name if self.chain_id else self.mapping.name)
        return f"{self.plan.name} step {self.order}: {target}"

    @property
    def rate_limit_per_minute(self):
        """rate_limit_per_second, expressed the way it's entered/displayed in
        the UI (requests/minute) — see static/js/rate_limit.js."""
        return round(self.rate_limit_per_second * 60, 2) if self.rate_limit_per_second else None
