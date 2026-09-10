import json
from datetime import timedelta

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models
from django.utils import timezone


def _fernet():
    return Fernet(settings.FIELD_ENCRYPTION_KEY)


def encrypt_dict(data: dict) -> str:
    raw = json.dumps(data or {}).encode()
    return _fernet().encrypt(raw).decode()


def decrypt_dict(token: str) -> dict:
    if not token:
        return {}
    raw = _fernet().decrypt(token.encode())
    return json.loads(raw.decode())


class Connection(models.Model):
    """One external system this platform can read from or write to.

    `auth_config` holds non-secret setup (client_id, authorize/token URLs,
    header names, scopes) so it's visible/editable in the UI. `secrets`
    holds anything sensitive (tokens, api keys, passwords) and is always
    stored encrypted and never serialized back to the client.
    """

    AUTH_OAUTH2 = "oauth2"
    AUTH_BASIC = "basic"
    AUTH_JWT = "jwt"
    AUTH_BEARER = "bearer"
    AUTH_NONE = "none"
    AUTH_TYPE_CHOICES = [
        (AUTH_OAUTH2, "OAuth2 (Authorization Code)"),
        (AUTH_BASIC, "Basic Auth"),
        (AUTH_JWT, "JWT (self-signed)"),
        (AUTH_BEARER, "Bearer Token"),
        (AUTH_NONE, "No auth"),
    ]

    name = models.CharField(max_length=120, unique=True)
    base_url = models.URLField(help_text="Root URL of the external system's API, e.g. https://api.example.com")
    auth_type = models.CharField(max_length=20, choices=AUTH_TYPE_CHOICES, default=AUTH_BEARER)
    auth_config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "OAuth2: client_id, client_secret, authorize_url, token_url, scope. "
            "Bearer: header_name (default Authorization), prefix (default Bearer). "
            "JWT: claims (dict, 'exp' filled in automatically), ttl_seconds (default 3600), "
            "algorithm (default HS256). Basic: no config needed."
        ),
    )
    secrets_encrypted = models.TextField(blank=True, default="")
    use_custom_headers = models.BooleanField(
        default=False,
        help_text="When on, custom_headers is sent with every request on top of whatever auth_type "
                   "already adds (Bearer/JWT/etc.) — off just leaves custom_headers unused, so values "
                   "already entered aren't lost by unchecking this.",
    )
    custom_headers = models.JSONField(
        default=dict, blank=True, help_text="{\"header-name\": \"value\", ...} — only applied when use_custom_headers is on.",
    )
    use_custom_params = models.BooleanField(
        default=False,
        help_text="When on, custom_params is added to every request's query string on top of whatever "
                   "the call itself already passes — off just leaves custom_params unused.",
    )
    custom_params = models.JSONField(
        default=dict, blank=True, help_text="{\"param-name\": \"value\", ...} — only applied when use_custom_params is on.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def secrets(self) -> dict:
        return decrypt_dict(self.secrets_encrypted)

    @secrets.setter
    def secrets(self, data: dict):
        self.secrets_encrypted = encrypt_dict(data)

    def merge_secrets(self, data: dict):
        current = self.secrets
        current.update({k: v for k, v in data.items() if v not in (None, "")})
        self.secrets = current

    @property
    def is_connected(self) -> bool:
        if self.auth_type == self.AUTH_NONE:
            return True
        secrets = self.secrets
        if self.auth_type == self.AUTH_OAUTH2:
            return bool(secrets.get("access_token"))
        if self.auth_type == self.AUTH_BEARER:
            return bool(secrets.get("token"))
        if self.auth_type == self.AUTH_JWT:
            return bool(secrets.get("signing_secret"))
        if self.auth_type == self.AUTH_BASIC:
            return bool(secrets.get("username"))
        return False


class TokenRefreshJob(models.Model):
    """Proactively refreshes an OAuth2 Connection's access token on a fixed
    interval via connections/scheduler.py, independent of whether any
    migration run happens to need it. Without this, a connection that sits
    idle longer than its *refresh* token's own lifetime (e.g. Tiny ERP:
    access token 4h, refresh token 1 day) would silently go stale and need a
    full re-authorization instead of a cheap refresh_token grant call."""

    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(STATUS_SUCCESS, "success"), (STATUS_FAILED, "failed")]

    connection = models.OneToOneField(Connection, on_delete=models.CASCADE, related_name="token_refresh_job")
    is_enabled = models.BooleanField(default=True)
    interval_minutes = models.PositiveIntegerField(
        default=180,
        help_text="How often to proactively refresh. Keep it comfortably under both the access "
                  "token's lifetime and the refresh token's own lifetime (e.g. Tiny ERP: access "
                  "token lasts 4h, refresh token 1 day — every 180 min refreshes well inside both).",
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=10, choices=STATUS_CHOICES, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Refresh job for {self.connection.name} ({'on' if self.is_enabled else 'off'})"

    def schedule_next_run(self):
        self.next_run_at = timezone.now() + timedelta(minutes=self.interval_minutes)


REDACTED_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}
BODY_LOG_LIMIT = 20000


def redact_headers(headers: dict) -> dict:
    return {k: ("***redacted***" if k.lower() in REDACTED_HEADERS else v) for k, v in (headers or {}).items()}


def truncate_body(body) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    body = str(body)
    return body if len(body) <= BODY_LOG_LIMIT else body[:BODY_LOG_LIMIT] + f"... [truncated, {len(body)} bytes total]"


class ApiCallLog(models.Model):
    """One outbound HTTP call made through ConnectionClient.request() (every
    connections.client get/post/put/patch/delete call, plus OAuth2 token
    refresh — see client.py) — full request/response capture for debugging
    exactly what was sent to and received from an external API, e.g. a
    migration write that 404s with no other clue why. Secrets in headers
    (Authorization, Cookie, ...) are redacted before saving; bodies are
    truncated past BODY_LOG_LIMIT to keep rows bounded."""

    connection = models.ForeignKey(
        Connection, on_delete=models.SET_NULL, null=True, blank=True, related_name="api_call_logs",
    )
    run = models.ForeignKey(
        "jobs.MigrationRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="api_call_logs",
        help_text="Set when this call happened inside a migration run's read/write step.",
    )
    method = models.CharField(max_length=10)
    url = models.CharField(max_length=1000)
    request_headers = models.JSONField(default=dict, blank=True)
    request_body = models.TextField(blank=True)
    status_code = models.IntegerField(null=True, blank=True)
    response_headers = models.JSONField(default=dict, blank=True)
    response_body = models.TextField(blank=True)
    error = models.TextField(blank=True, help_text="Set instead of status_code/response_* when the request itself failed (timeout, DNS, connection refused, ...).")
    duration_ms = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.method} {self.url} -> {self.status_code or self.error or '?'}"
