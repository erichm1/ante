from django.db import models


class CallChain(models.Model):
    """An ordered sequence of raw API calls against one Connection, where a
    later step's path/body can reference an earlier step's parsed JSON
    response — e.g. create a customer, then use its returned `id` to create
    an order for that customer, all in one click. Unlike Mapping (bulk,
    record-by-record ETL), a chain runs a small, fixed number of one-off
    calls — see chains/executor.py."""

    name = models.CharField(max_length=120)
    connection = models.ForeignKey(
        "connections.Connection", on_delete=models.CASCADE, related_name="call_chains",
        help_text="Every step in this chain calls this connection's API, using its configured auth.",
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class CallChainStep(models.Model):
    METHOD_GET = "GET"
    METHOD_POST = "POST"
    METHOD_PUT = "PUT"
    METHOD_PATCH = "PATCH"
    METHOD_DELETE = "DELETE"
    METHOD_CHOICES = [
        (METHOD_GET, "GET"), (METHOD_POST, "POST"), (METHOD_PUT, "PUT"),
        (METHOD_PATCH, "PATCH"), (METHOD_DELETE, "DELETE"),
    ]

    chain = models.ForeignKey(CallChain, on_delete=models.CASCADE, related_name="steps")
    name = models.SlugField(
        max_length=50,
        help_text="Referenced by later steps as {{this_name.some.json.path}} — must be unique within the chain.",
    )
    order = models.PositiveIntegerField()
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default=METHOD_GET)
    path = models.CharField(
        max_length=500,
        help_text="Endpoint path, relative to the chain's connection base_url. May reference a prior "
                   "step's response with {{step_name.field.path}}, e.g. /orders/{{create_customer.id}}.",
    )
    body = models.TextField(
        blank=True,
        help_text="Optional JSON request body template. {{step_name.field.path}} placeholders are "
                   "replaced with the referenced value (as real JSON — a number stays a number), then "
                   "the whole thing is parsed as JSON, e.g. {\"customer_id\": {{create_customer.id}}}.",
    )
    captures = models.JSONField(
        default=list, blank=True,
        help_text="Named values pulled out of THIS step's response for later steps to reference by a "
                   "short name instead of the full {{this_name.some.json.path}} — a list of "
                   "{\"name\": \"customer_id\", \"path\": \"id\"} objects (path may be blank to capture "
                   "the whole response). Once captured, {{customer_id}} works anywhere {{create_customer."
                   "id}} would (see chains/executor.py::apply_captures) — same context dict either way.",
    )

    class Meta:
        ordering = ["order"]
        unique_together = [("chain", "order"), ("chain", "name")]

    def __str__(self):
        return f"{self.chain.name} step {self.order}: {self.name}"


class CallChainRun(models.Model):
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(STATUS_SUCCESS, "success"), (STATUS_FAILED, "failed")]

    chain = models.ForeignKey(CallChain, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result_file = models.FileField(
        upload_to="chains/results/%Y/%m/", null=True, blank=True,
        help_text="Every step's parsed JSON response, keyed by step name — this is exactly what later "
                   "steps' {{name.path}} placeholders are resolved against (see chains/executor.py), "
                   "saved to a real file so it can be read back afterward for the same purpose.",
    )

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.chain.name} run #{self.pk} ({self.status})"


class CallChainStepResult(models.Model):
    """One executed step's outcome — step fields are denormalized here (not
    just a FK) so a run's history stays meaningful even after the step
    itself is edited or removed from the chain."""

    run = models.ForeignKey(CallChainRun, on_delete=models.CASCADE, related_name="step_results")
    step = models.ForeignKey(CallChainStep, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    order = models.PositiveIntegerField()
    name = models.CharField(max_length=50)
    method = models.CharField(max_length=10)
    resolved_path = models.CharField(max_length=500, blank=True)
    resolved_body = models.TextField(blank=True)
    status_code = models.IntegerField(null=True, blank=True)
    response_json = models.JSONField(null=True, blank=True)
    captured_variables = models.JSONField(
        default=dict, blank=True,
        help_text="{variable_name: extracted_value} for every capture defined on this step, as it "
                   "actually resolved on this run.",
    )
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.name} -> {self.status_code or self.error or '?'}"
