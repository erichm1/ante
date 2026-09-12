from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


ITIL_TICKET_SLA = {
    "critical": {"response_minutes": 30,  "resolution_hours": 4},
    "high":     {"response_minutes": 120, "resolution_hours": 8},
    "medium":   {"response_minutes": 240, "resolution_hours": 24},
    "low":      {"response_minutes": 480, "resolution_hours": 72},
}


class Ticket(models.Model):
    PRIORITY_CRITICAL = "critical"
    PRIORITY_HIGH     = "high"
    PRIORITY_MEDIUM   = "medium"
    PRIORITY_LOW      = "low"
    PRIORITY_CHOICES = [
        (PRIORITY_CRITICAL, "Critical"),
        (PRIORITY_HIGH,     "High"),
        (PRIORITY_MEDIUM,   "Medium"),
        (PRIORITY_LOW,      "Low"),
    ]

    STATUS_OPEN        = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_RESOLVED    = "resolved"
    STATUS_CLOSED      = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN,        "Open"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_RESOLVED,    "Resolved"),
        (STATUS_CLOSED,      "Closed"),
    ]

    title       = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    priority    = models.CharField(max_length=12, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    status      = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_OPEN)
    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tickets",
    )
    incident = models.ForeignKey(
        "incidents.Incident",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="tickets",
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    # SLA (set on first save from ITIL targets)
    sla_response_deadline   = models.DateTimeField(null=True, blank=True)
    sla_resolution_deadline = models.DateTimeField(null=True, blank=True)
    acknowledged_at         = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.pk:
            sla = ITIL_TICKET_SLA.get(self.priority, ITIL_TICKET_SLA["medium"])
            now = timezone.now()
            self.sla_response_deadline   = now + timedelta(minutes=sla["response_minutes"])
            self.sla_resolution_deadline = now + timedelta(hours=sla["resolution_hours"])
        super().save(*args, **kwargs)

    @property
    def is_closed(self):
        return self.status in (self.STATUS_RESOLVED, self.STATUS_CLOSED)

    def _sla_status(self, deadline, achieved_at):
        if achieved_at and deadline:
            return "ok" if achieved_at <= deadline else "breached"
        if not deadline:
            return "ok"
        now = timezone.now()
        remaining = (deadline - now).total_seconds()
        if remaining <= 0:
            return "breached"
        total = (deadline - self.created_at).total_seconds()
        if total > 0 and remaining / total < 0.2:
            return "warning"
        return "ok"

    @property
    def sla_response_status(self):
        return self._sla_status(self.sla_response_deadline, self.acknowledged_at)

    @property
    def sla_resolution_status(self):
        return self._sla_status(self.sla_resolution_deadline, self.resolved_at)


class TicketNote(models.Model):
    ticket     = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="notes")
    body       = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Note on {self.ticket.title}"
