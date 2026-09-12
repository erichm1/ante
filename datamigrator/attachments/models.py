from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB


def _upload_path(instance, filename):
    if instance.incident_id:
        return f"attachments/incidents/{instance.incident_id}/{filename}"
    return f"attachments/tickets/{instance.ticket_id}/{filename}"


def _validate_size(file):
    if file.size > MAX_ATTACHMENT_BYTES:
        raise ValidationError(f"File too large — maximum is {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.")


class Attachment(models.Model):
    incident = models.ForeignKey(
        "incidents.Incident",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="attachments",
    )
    ticket = models.ForeignKey(
        "tickets.Ticket",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="attachments",
    )
    file        = models.FileField(upload_to=_upload_path, validators=[_validate_size])
    name        = models.CharField(max_length=255, blank=True)
    size        = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True,
        related_name="attachments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.name or self.file.name

    def save(self, *args, **kwargs):
        if not self.name and self.file:
            self.name = self.file.name.split("/")[-1]
        if self.file and not self.size:
            self.size = self.file.size
        super().save(*args, **kwargs)

    def clean(self):
        if not self.incident_id and not self.ticket_id:
            raise ValidationError("An attachment must belong to an incident or a ticket.")
