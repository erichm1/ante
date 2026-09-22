from django.conf import settings
from django.db import models
from django.utils import timezone


class NotificationQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(recipient=user)

    def unread(self):
        return self.filter(read_at__isnull=True)

    def mark_all_read(self):
        """Mark every unread notification in this queryset as read; returns how many were changed.
        (`Notification.objects.for_user(user).mark_all_read()` is "mark all of this person's notifications read".)"""
        return self.unread().update(read_at=timezone.now())

    def mark_read(self, ids):
        """Mark just these notifications read (ids the queryset doesn't contain are ignored)."""
        return self.filter(pk__in=list(ids)).unread().update(read_at=timezone.now())


class Notification(models.Model):
    """One in-app notification for one person: a run, chain or plan (or scheduled job) has finished.

    outcome — what happened:
      success    it completed
      failed     it ended with errors
      cancelled  it was called off before it started (a queued or scheduled run cancelled)
      killed     it was running and was stopped with the Kill button (or was found orphaned and closed)
    """

    KIND_RUN, KIND_CHAIN, KIND_PLAN = "run", "chain", "plan"
    KIND_CHOICES = [(KIND_RUN, "Migration run"), (KIND_CHAIN, "Chain run"), (KIND_PLAN, "Plan")]

    OUTCOME_SUCCESS, OUTCOME_FAILED, OUTCOME_CANCELLED, OUTCOME_KILLED = "success", "failed", "cancelled", "killed"
    OUTCOME_CHOICES = [(o, o) for o in (OUTCOME_SUCCESS, OUTCOME_FAILED, OUTCOME_CANCELLED, OUTCOME_KILLED)]

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    outcome = models.CharField(max_length=10, choices=OUTCOME_CHOICES)
    title = models.CharField(max_length=200)
    message = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=300, blank=True, help_text="Where the notification takes you when clicked.")
    source_id = models.PositiveIntegerField(null=True, blank=True, help_text="Id of the run / chain run / plan it is about.")
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["recipient", "read_at"])]

    def __str__(self):
        return f"{self.recipient} · {self.title}"

    @property
    def level(self):
        """Colour class: ok / err / warn — cancelled and killed are neither a success nor a failure."""
        return {"success": "ok", "failed": "err"}.get(self.outcome, "warn")
