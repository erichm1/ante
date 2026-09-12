from django.db import models


class Report(models.Model):
    """A saved, reusable data export — an ordered list of ReportSections,
    each pulling one Entity's data down to a chosen subset/order of its
    Fields. Exported as one combined CSV (see reports/exporter.py) or
    previewed on screen before exporting — both read the same section/
    field_ids definition, so what you see in the preview is exactly what
    the CSV contains."""

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class ReportSection(models.Model):
    """One Entity's contribution to a Report — its own section in the
    combined CSV/preview (see reports/exporter.py::build_report_csv). Built
    up via drag-and-drop on the report's detail page: dragging an Entity in
    creates the section; dragging its Fields into the section (and
    reordering them) rewrites `field_ids`."""

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="sections")
    entity = models.ForeignKey("schemas.Entity", on_delete=models.CASCADE, related_name="+")
    order = models.PositiveIntegerField()
    field_ids = models.JSONField(
        default=list, blank=True,
        help_text="This entity's selected columns, in the order they'll appear in the CSV/preview — a "
                   "plain list of schemas.Field ids (validated against the section's own entity in "
                   "reports/views.py, not enforced at the DB level).",
    )

    class Meta:
        ordering = ["order"]
        unique_together = ("report", "order")

    def __str__(self):
        return f"{self.report.name} — {self.entity.name}"
