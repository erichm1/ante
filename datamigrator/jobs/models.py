from django.db import models


class MigrationRun(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(s, s) for s in (STATUS_PENDING, STATUS_RUNNING, STATUS_SUCCESS, STATUS_FAILED)]

    mapping = models.ForeignKey("mappings.Mapping", on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    records_read = models.IntegerField(default=0)
    records_written = models.IntegerField(default=0)
    records_failed = models.IntegerField(default=0)
    requests_made = models.IntegerField(
        default=0, help_text="Every HTTP call to a source or destination system, source reads and destination writes alike.",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Run #{self.pk} ({self.mapping.name})"


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
