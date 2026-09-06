import json

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models


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
    AUTH_API_KEY = "api_key"
    AUTH_BASIC = "basic"
    AUTH_NONE = "none"
    AUTH_TYPE_CHOICES = [
        (AUTH_OAUTH2, "OAuth2 (Authorization Code)"),
        (AUTH_API_KEY, "API Key / Bearer Token"),
        (AUTH_BASIC, "Basic Auth"),
        (AUTH_NONE, "No auth"),
    ]

    name = models.CharField(max_length=120, unique=True)
    base_url = models.URLField(help_text="Root URL of the external system's API, e.g. https://api.example.com")
    auth_type = models.CharField(max_length=20, choices=AUTH_TYPE_CHOICES, default=AUTH_API_KEY)
    auth_config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "OAuth2: client_id, client_secret, authorize_url, token_url, scope. "
            "API key: header_name (default Authorization), prefix (default Bearer). "
            "Basic: no config needed."
        ),
    )
    secrets_encrypted = models.TextField(blank=True, default="")
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
        if self.auth_type == self.AUTH_API_KEY:
            return bool(secrets.get("api_key"))
        if self.auth_type == self.AUTH_BASIC:
            return bool(secrets.get("username"))
        return False
