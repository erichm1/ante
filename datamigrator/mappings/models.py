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


class EntityMapping(models.Model):
    """One entity-to-entity link inside a Mapping, e.g. Source.Customer -> Target.Contact."""

    mapping = models.ForeignKey(Mapping, on_delete=models.CASCADE, related_name="entity_mappings")
    source_entity = models.ForeignKey("schemas.Entity", on_delete=models.CASCADE, related_name="+")
    target_entity = models.ForeignKey("schemas.Entity", on_delete=models.CASCADE, related_name="+")

    class Meta:
        unique_together = ("mapping", "source_entity", "target_entity")

    def __str__(self):
        return f"{self.source_entity} -> {self.target_entity}"


class FieldMapping(models.Model):
    """One field-to-field connection drawn on the canvas."""

    entity_mapping = models.ForeignKey(EntityMapping, on_delete=models.CASCADE, related_name="field_mappings")
    source_field = models.ForeignKey("schemas.Field", on_delete=models.CASCADE, related_name="+")
    target_field = models.ForeignKey("schemas.Field", on_delete=models.CASCADE, related_name="+")
    transform = models.CharField(
        max_length=255, blank=True,
        help_text="Optional Python expression applied to `value` before writing, e.g. value.upper() or value[:10]",
    )

    class Meta:
        unique_together = ("entity_mapping", "source_field", "target_field")

    def __str__(self):
        return f"{self.source_field} -> {self.target_field}"
