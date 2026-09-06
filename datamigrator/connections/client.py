import time

import requests
from requests.auth import HTTPBasicAuth

from .models import Connection


class ConnectionClient:
    """Calls a Connection's external API using whichever auth it's configured for.

    Usage:
        client = ConnectionClient(connection)
        resp = client.get("/customers")
        resp = client.post("/orders", json={...})

    OAuth2 tokens are refreshed automatically, just before they expire,
    using the refresh_token grant.
    """

    def __init__(self, connection: Connection):
        self.connection = connection
        self.session = requests.Session()

    def _apply_api_key(self):
        secrets = self.connection.secrets
        config = self.connection.auth_config or {}
        header_name = config.get("header_name", "Authorization")
        prefix = config.get("prefix", "Bearer")
        value = secrets.get("api_key", "")
        self.session.headers[header_name] = f"{prefix} {value}".strip() if prefix else value

    def _apply_basic(self):
        secrets = self.connection.secrets
        self.session.auth = HTTPBasicAuth(secrets.get("username", ""), secrets.get("password", ""))

    def _apply_oauth2(self):
        secrets = self.connection.secrets
        if self._token_expired(secrets):
            secrets = self._refresh_oauth2(secrets)
        self.session.headers["Authorization"] = f"Bearer {secrets.get('access_token', '')}"

    @staticmethod
    def _token_expired(secrets: dict) -> bool:
        expires_at = secrets.get("expires_at")
        if not expires_at:
            return False
        return time.time() >= (expires_at - 30)  # refresh 30s early

    def _refresh_oauth2(self, secrets: dict) -> dict:
        config = self.connection.auth_config or {}
        token_url = config.get("token_url")
        refresh_token = secrets.get("refresh_token")
        if not token_url or not refresh_token:
            return secrets

        resp = requests.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": config.get("client_id", ""),
                "client_secret": config.get("client_secret", ""),
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        secrets = {
            **secrets,
            "access_token": payload.get("access_token", secrets.get("access_token")),
            "refresh_token": payload.get("refresh_token", refresh_token),
            "expires_at": time.time() + payload.get("expires_in", 3600),
        }
        self.connection.secrets = secrets
        self.connection.save(update_fields=["secrets_encrypted"])
        return secrets

    def _prepare(self):
        if self.connection.auth_type == Connection.AUTH_API_KEY:
            self._apply_api_key()
        elif self.connection.auth_type == Connection.AUTH_BASIC:
            self._apply_basic()
        elif self.connection.auth_type == Connection.AUTH_OAUTH2:
            self._apply_oauth2()
        return self.session

    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.connection.base_url.rstrip("/") + "/" + path.lstrip("/")

    def request(self, method: str, path: str, **kwargs):
        session = self._prepare()
        timeout = kwargs.pop("timeout", 30)
        return session.request(method, self._build_url(path), timeout=timeout, **kwargs)

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs):
        return self.request("PATCH", path, **kwargs)
