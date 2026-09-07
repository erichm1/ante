from django.db import models


class Entity(models.Model):
    """A resource/table on a Connection, e.g. 'Customer' at /customers."""

    SOURCE_MANUAL = "manual"
    SOURCE_OPENAPI = "openapi"
    SOURCE_SAMPLED = "sampled"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_OPENAPI, "OpenAPI import"),
        (SOURCE_SAMPLED, "Sampled from response"),
    ]

    connection = models.ForeignKey("connections.Connection", on_delete=models.CASCADE, related_name="entities")
    name = models.CharField(max_length=120)
    endpoint_path = models.CharField(
        max_length=255, blank=True,
        help_text="Path used to read/write records, relative to the connection's base URL, e.g. /customers. "
                   "Unused when source_file is set — records come from the file instead of an HTTP call.",
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    source_file = models.FileField(
        upload_to="schemas/source_files/%Y/%m/", null=True, blank=True,
        help_text="A CSV or .xlsx file this entity's records come from directly — jobs/engine.py reads "
                   "every row from here instead of making an HTTP request, so a mapping can use this "
                   "entity as its source with no real API/connection behind it at all (see connections/"
                   "detail.html's 'From file' discovery tab, which sets this and tags source='manual').",
    )
    canvas_x = models.IntegerField(default=40)
    canvas_y = models.IntegerField(default=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("connection", "name")
        ordering = ["name"]
        verbose_name_plural = "entities"

    def __str__(self):
        return f"{self.connection.name}.{self.name}"


class Field(models.Model):
    TYPE_STRING = "string"
    TYPE_NUMBER = "number"
    TYPE_INTEGER = "integer"
    TYPE_BOOLEAN = "boolean"
    TYPE_OBJECT = "object"
    TYPE_ARRAY = "array"
    TYPE_DATE = "date"
    TYPE_CHOICES = [
        (TYPE_STRING, "string"), (TYPE_NUMBER, "number"), (TYPE_INTEGER, "integer"),
        (TYPE_BOOLEAN, "boolean"), (TYPE_OBJECT, "object"), (TYPE_ARRAY, "array"), (TYPE_DATE, "date"),
    ]

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="fields")
    name = models.CharField(max_length=120)
    field_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_STRING)
    required = models.BooleanField(default=False)
    sample_value = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("entity", "name")
        ordering = ["name"]

    def __str__(self):
        return f"{self.entity.name}.{self.name}"
