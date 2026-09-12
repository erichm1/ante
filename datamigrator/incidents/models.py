from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


# ITIL P1-P4 SLA targets for incidents
ITIL_INCIDENT_SLA = {
    "critical": {"response_minutes": 15,  "resolution_hours": 4},
    "high":     {"response_minutes": 60,  "resolution_hours": 8},
    "medium":   {"response_minutes": 240, "resolution_hours": 24},
    "low":      {"response_minutes": 480, "resolution_hours": 72},
}

SERVICE_CHOICES = [
    ("connections",  "Connections"),
    ("integrations", "Integrations"),
    ("mappings",     "Mappings"),
    ("jobs",         "Runs / Jobs"),
    ("plans",        "Plans"),
    ("chains",       "Chains"),
    ("reports",      "Reports"),
    ("tickets",      "Tickets"),
    ("api",          "API Gateway"),
    ("auth",         "Authentication"),
]


class Incident(models.Model):
    SEV_CRITICAL = "critical"
    SEV_HIGH     = "high"
    SEV_MEDIUM   = "medium"
    SEV_LOW      = "low"
    SEVERITY_CHOICES = [
        (SEV_CRITICAL, "Critical"),
        (SEV_HIGH,     "High"),
        (SEV_MEDIUM,   "Medium"),
        (SEV_LOW,      "Low"),
    ]

    STATUS_OPEN          = "open"
    STATUS_INVESTIGATING = "investigating"
    STATUS_IDENTIFIED    = "identified"
    STATUS_RESOLVED      = "resolved"
    STATUS_CHOICES = [
        (STATUS_OPEN,          "Open"),
        (STATUS_INVESTIGATING, "Investigating"),
        (STATUS_IDENTIFIED,    "Identified"),
        (STATUS_RESOLVED,      "Resolved"),
    ]

    title             = models.CharField(max_length=200)
    description       = models.TextField(blank=True)
    severity          = models.CharField(max_length=12, choices=SEVERITY_CHOICES, default=SEV_MEDIUM)
    status            = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_OPEN)
    connection        = models.ForeignKey(
        "connections.Connection",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="incidents",
    )
    affected_services = models.JSONField(default=list, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)
    resolved_at       = models.DateTimeField(null=True, blank=True)
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
            sla = ITIL_INCIDENT_SLA.get(self.severity, ITIL_INCIDENT_SLA["medium"])
            now = timezone.now()
            self.sla_response_deadline   = now + timedelta(minutes=sla["response_minutes"])
            self.sla_resolution_deadline = now + timedelta(hours=sla["resolution_hours"])
        super().save(*args, **kwargs)

    @property
    def is_resolved(self):
        return self.status == self.STATUS_RESOLVED

    def _sla_status(self, deadline, achieved_at, closed):
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
        return self._sla_status(self.sla_response_deadline, self.acknowledged_at, False)

    @property
    def sla_resolution_status(self):
        return self._sla_status(self.sla_resolution_deadline, self.resolved_at, self.is_resolved)


class IncidentNote(models.Model):
    incident   = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="notes")
    body       = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Note on {self.incident.title}"


class PostMortem(models.Model):
    incident              = models.OneToOneField(Incident, on_delete=models.CASCADE, related_name="postmortem")
    summary               = models.TextField(blank=True)
    timeline              = models.TextField(blank=True)
    impact                = models.TextField(blank=True)
    root_cause            = models.TextField(blank=True)
    contributing_factors  = models.TextField(blank=True)
    what_went_well        = models.TextField(blank=True)
    what_went_wrong       = models.TextField(blank=True)
    action_items          = models.TextField(blank=True)
    lessons_learned       = models.TextField(blank=True)
    written_by            = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="postmortems",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Post-mortem: {self.incident.title}"


class IncidentRule(models.Model):
    TRIGGER_RUN_FAIL  = "run_fail_rate"
    TRIGGER_CONN_ERR  = "connection_error"
    TRIGGER_API_ERROR = "api_error_rate"
    TRIGGER_CHOICES = [
        (TRIGGER_RUN_FAIL,  "Run failure rate exceeds threshold"),
        (TRIGGER_CONN_ERR,  "Connection API error rate exceeds threshold"),
        (TRIGGER_API_ERROR, "Global API error rate exceeds threshold"),
    ]

    user         = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="incident_rules"
    )
    name         = models.CharField(max_length=200)
    trigger_type = models.CharField(max_length=30, choices=TRIGGER_CHOICES)
    threshold    = models.FloatField(default=50.0, help_text="Percentage (0–100)")
    window_hours = models.PositiveIntegerField(default=1, help_text="Look-back window in hours")
    connection   = models.ForeignKey(
        "connections.Connection",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="incident_rules",
    )
    severity   = models.CharField(
        max_length=12, choices=Incident.SEVERITY_CHOICES, default=Incident.SEV_MEDIUM
    )
    auto_title = models.CharField(max_length=200, blank=True, help_text="Leave blank to auto-generate")
    enabled    = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_checked_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name
