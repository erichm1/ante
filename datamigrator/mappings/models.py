from django.db import models


class Mapping(models.Model):
    """A migration project: move data from one origin connection to one or
    more destination connections. The origin and destinations are chosen
    upfront, before any entity/field mapping happens — the canvas then only
    lets you pick entities that actually belong to those connections."""

    name = models.CharField(max_length=120)
    source_connection = models.ForeignKey(
        "connections.Connection", on_delete=models.CASCADE, related_name="mappings_as_source",
        help_text="The integration origin.",
    )
    destination_connections = models.ManyToManyField(
        "connections.Connection", related_name="mappings_as_destination", blank=True,
        help_text="The integration destinies. Add more than one to fan the same source out to several systems.",
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def default_rate_limit_per_second(self):
        """Falls back to the slowest connection involved (source or any
        destination) when a run doesn't set its own rate_limit_per_second —
        lets a Connection's own rate limit (set on its edit page) act as a
        standing default so every run against it stays under whatever cap
        the external API actually enforces, without having to remember to
        set it per run. See jobs/engine.py::run_migration's throttle()."""
        limits = [
            c.rate_limit_per_second
            for c in [self.source_connection, *self.destination_connections.all()]
            if c and c.rate_limit_per_second
        ]
        return min(limits) if limits else None


class EntityMapping(models.Model):
    """One entity-to-entity link inside a Mapping, e.g. Source.Customer -> Target.Contact."""

    METHOD_GET = "GET"
    METHOD_POST = "POST"
    METHOD_PUT = "PUT"
    METHOD_PATCH = "PATCH"
    METHOD_DELETE = "DELETE"
    METHOD_CHOICES = [
        (METHOD_GET, "GET"), (METHOD_POST, "POST"), (METHOD_PUT, "PUT"),
        (METHOD_PATCH, "PATCH"), (METHOD_DELETE, "DELETE"),
    ]

    mapping = models.ForeignKey(Mapping, on_delete=models.CASCADE, related_name="entity_mappings")
    source_entity = models.ForeignKey("schemas.Entity", on_delete=models.CASCADE, related_name="+")
    target_entity = models.ForeignKey("schemas.Entity", on_delete=models.CASCADE, related_name="+")
    write_method = models.CharField(
        max_length=10, choices=METHOD_CHOICES, default=METHOD_POST,
        help_text="HTTP verb used to write each mapped record to the target entity's endpoint_path (jobs/engine.py).",
    )

    class Meta:
        unique_together = ("mapping", "source_entity", "target_entity")

    def __str__(self):
        return f"{self.source_entity} -> {self.target_entity}"


class FieldMapping(models.Model):
    """One field-to-field connection drawn on the canvas."""

    entity_mapping = models.ForeignKey(EntityMapping, on_delete=models.CASCADE, related_name="field_mappings")
    source_field = models.ForeignKey("schemas.Field", on_delete=models.CASCADE, related_name="+")
    target_field = models.ForeignKey("schemas.Field", on_delete=models.CASCADE, related_name="+")
    transform_rules = models.JSONField(
        default=list, blank=True,
        help_text="No-code transform steps applied in order before writing (uppercase/lowercase/trim, "
                   "map specific values, default-if-empty) — see jobs/engine.py::_apply_rules for the "
                   "exact rule shapes. The friendly alternative to writing a raw `transform` expression; "
                   "applied first, then `transform` runs on the result if also set.",
    )
    transform = models.CharField(
        max_length=255, blank=True,
        help_text="Advanced/optional: a Python expression applied to `value` after transform_rules, "
                   "e.g. value.upper() or value[:10] — for anything the no-code rules above can't express.",
    )

    class Meta:
        unique_together = ("entity_mapping", "source_field", "target_field")

    def __str__(self):
        return f"{self.source_field} -> {self.target_field}"
