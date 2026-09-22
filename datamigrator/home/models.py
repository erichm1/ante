from django.db import models


class StatusCheckResult(models.Model):
    """One health check of the status page — the database, a background worker, or one of Ante's own pages and API
    endpoints — kept so the page can show a 24h uptime and a small history per check rather than only a live snapshot.
    Written by `manage.py run_status_checks` (schedule it) and, at most every few minutes, when the page is viewed
    (home/status.py::record_if_stale). Same idea as the status page of the callum_freight_hub project."""

    class State(models.TextChoices):
        OPERATIONAL = "OPERATIONAL", "Operational"
        DEGRADED = "DEGRADED", "Degraded"
        DOWN = "DOWN", "Down"

    group = models.CharField(max_length=50)
    name = models.CharField(max_length=100)
    method = models.CharField(max_length=10, default="GET")
    path = models.CharField(max_length=255, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    latency_ms = models.FloatField(null=True, blank=True)
    state = models.CharField(max_length=12, choices=State.choices)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["group", "name", "-created_at"])]

    def __str__(self):
        return f"{self.group} / {self.name}: {self.state}"
