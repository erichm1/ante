from django.db import models


class MigrationRun(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(s, s) for s in (STATUS_PENDING, STATUS_RUNNING, STATUS_SUCCESS, STATUS_FAILED)]

    mapping = models.ForeignKey("mappings.Mapping", on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    retry_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="retries",
        help_text="Set when this run was created to retry failed entity mappings from a prior run.",
    )
    retry_resolved = models.BooleanField(
        default=False,
        help_text="Set to True on the original failed run when one of its retries completes successfully.",
    )
    records_read = models.IntegerField(default=0)
    records_written = models.IntegerField(default=0)
    records_failed = models.IntegerField(default=0)
    requests_made = models.IntegerField(
        default=0, help_text="Every HTTP call to a source or destination system, source reads and destination writes alike.",
    )
    rate_limit_per_second = models.FloatField(
        null=True, blank=True,
        help_text="Caps outbound requests/second (source reads and destination writes alike) — "
                   "jobs/engine.py sleeps between calls to stay under it. Blank/null = unthrottled.",
    )
    scheduled_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Set when this run was scheduled for a future time instead of started immediately "
                   "(see jobs/scheduler.py) — null for a run that started right away.",
    )
    started_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Set at creation time; for a scheduled run this is overwritten with the real start "
                   "time once jobs/scheduler.py actually kicks it off.",
    )
    finished_at = models.DateTimeField(null=True, blank=True)
    input_file = models.FileField(
        upload_to="jobs/run_inputs/%Y/%m/", null=True, blank=True,
        help_text="A CSV/XLSX uploaded just for this run, read instead of the mapping's source "
                   "entity's own stored source_file — lets the same already-built mapping (fields, "
                   "transforms, everything) run against fresh data each time without re-uploading "
                   "through the connection's 'From file' discovery tab (which would also redo "
                   "schema/field detection). Ignored for a source entity with no source_file at all "
                   "(i.e. a real API source) — see jobs/engine.py::records_for.",
    )

    @property
    def rate_limit_per_minute(self):
        """rate_limit_per_second, expressed the way it's entered/displayed in
        the UI (requests/minute) — see static/js/rate_limit.js."""
        return round(self.rate_limit_per_second * 60, 2) if self.rate_limit_per_second else None

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Run #{self.pk} ({self.mapping.name})"


class RunStepStatus(models.Model):
    """One node in a run's live "pipeline" view (see jobs/views.py::run_snapshot)
    — one row per EntityMapping actually touched by a run, tracking that
    pair's own status/counts independently of the run-level totals on
    MigrationRun. Updated in place by jobs/engine.py as each entity pair is
    processed, so the snapshot page can poll GET /api/runs/<id>/ and show
    per-step ⏳/▶/✅/❌ icons the same way GitHub Actions shows per-job status."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(s, s) for s in (STATUS_PENDING, STATUS_RUNNING, STATUS_SUCCESS, STATUS_FAILED)]

    run = models.ForeignKey(MigrationRun, on_delete=models.CASCADE, related_name="step_statuses")
    entity_mapping = models.ForeignKey("mappings.EntityMapping", on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    records_read = models.IntegerField(default=0)
    records_written = models.IntegerField(default=0)
    records_failed = models.IntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=500, blank=True)

    class Meta:
        unique_together = ("run", "entity_mapping")
        ordering = ["id"]

    def __str__(self):
        return f"{self.run} · {self.entity_mapping} ({self.status})"


class MigrationLog(models.Model):
    LEVEL_INFO = "info"
    LEVEL_WARNING = "warning"
    LEVEL_ERROR = "error"
    LEVEL_CHOICES = [(l, l) for l in (LEVEL_INFO, LEVEL_WARNING, LEVEL_ERROR)]

    run = models.ForeignKey(MigrationRun, on_delete=models.CASCADE, related_name="logs")
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
