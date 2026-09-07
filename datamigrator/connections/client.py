import base64
import hashlib
import hmac
import json
import time

import requests
from requests.auth import HTTPBasicAuth

from .models import ApiCallLog, Connection, redact_headers, truncate_body


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign_jwt_hs256(claims: dict, secret: str) -> str:
    """Signs a compact HS256 JWT from stdlib only — no pyjwt dependency needed
    for a scaffold this size. `claims` should already have any static claims
    (sub, iss, ...); `exp`/`iat` are expected to already be filled in by the
    caller so this stays a pure signing function."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


class ConnectionClient:
    """Calls a Connection's external API using whichever auth it's configured for.

    Usage:
        client = ConnectionClient(connection)
        resp = client.get("/customers")
        resp = client.post("/orders", json={...})

    OAuth2 tokens are refreshed automatically, just before they expire,
    using the refresh_token grant.
    """

    def __init__(self, connection: Connection, run=None):
        self.connection = connection
        self.session = requests.Session()
        # Set by jobs/engine.py so every request made while executing a
        # MigrationRun is traceable back to it in ApiCallLog — None
        # everywhere else (discovery, OAuth refresh polling, ad-hoc admin
        # calls), where there's no run to correlate to.
        self.run = run

    def _apply_bearer(self):
        secrets = self.connection.secrets
        config = self.connection.auth_config or {}
        header_name = config.get("header_name", "Authorization")
        prefix = config.get("prefix", "Bearer")
        value = secrets.get("token", "")
        self.session.headers[header_name] = f"{prefix} {value}".strip() if prefix else value

    def _apply_jwt(self):
        secrets = self.connection.secrets
        config = self.connection.auth_config or {}
        signing_secret = secrets.get("signing_secret", "")
        if not secrets.get("jwt") or self._token_expired(secrets):
            claims = dict(config.get("claims") or {})
            ttl = config.get("ttl_seconds", 3600)
            now = int(time.time())
            claims.setdefault("iat", now)
            claims["exp"] = now + ttl
            token = sign_jwt_hs256(claims, signing_secret)
            secrets = {**secrets, "jwt": token, "expires_at": now + ttl}
            self.connection.secrets = secrets
            self.connection.save(update_fields=["secrets_encrypted"])
        self.session.headers["Authorization"] = f"Bearer {secrets.get('jwt', '')}"

    def _apply_basic(self):
        secrets = self.connection.secrets
        self.session.auth = HTTPBasicAuth(secrets.get("username", ""), secrets.get("password", ""))

    def _apply_oauth2(self):
        secrets = self.refresh_oauth2_token()
        self.session.headers["Authorization"] = f"Bearer {secrets.get('access_token', '')}"

    @staticmethod
    def _token_expired(secrets: dict) -> bool:
        expires_at = secrets.get("expires_at")
        if not expires_at:
            return False
        return time.time() >= (expires_at - 30)  # refresh 30s early

    def refresh_oauth2_token(self, force: bool = False) -> dict:
        """Refreshes this connection's OAuth2 access token via the
        refresh_token grant. Called lazily (force=False, right before a
        request when the token's about to expire) and proactively by
        connections/scheduler.py's TokenRefreshJob poller (force=True, on a
        fixed interval regardless of expiry) — same HTTP call either way,
        `force` only controls whether an unexpired token is left alone.
        Raises requests.HTTPError on a failed refresh (e.g. an expired
        refresh token), same as any other failed request in this client."""
        secrets = self.connection.secrets
        if not force and not self._token_expired(secrets):
            return secrets
        return self._refresh_oauth2(secrets)

    def _refresh_oauth2(self, secrets: dict) -> dict:
        config = self.connection.auth_config or {}
        token_url = config.get("token_url")
        refresh_token = secrets.get("refresh_token")
        if not token_url or not refresh_token:
            # Silent no-op for the lazy, not-actually-expired-yet caller (nothing
            # to do); a forced refresh needs a loud failure so a scheduled job
            # doesn't report "success" while doing nothing.
            missing = "token_url" if not token_url else "refresh_token"
            raise ValueError(f"Can't refresh — connection is missing {missing}.")

        # Goes through self._send (not self.request, and not a bare
        # requests.post) so the refresh call itself lands in ApiCallLog too —
        # self.request would call _prepare(), which for an OAuth2 connection
        # calls _apply_oauth2() -> refresh_oauth2_token() -> back here,
        # infinitely; _send() skips that and just makes + logs the call.
        resp = self._send(
            "POST",
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": config.get("client_id", ""),
                "client_secret": config.get("client_secret", ""),
            },
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
        if self.connection.auth_type == Connection.AUTH_BEARER:
            self._apply_bearer()
        elif self.connection.auth_type == Connection.AUTH_JWT:
            self._apply_jwt()
        elif self.connection.auth_type == Connection.AUTH_BASIC:
            self._apply_basic()
        elif self.connection.auth_type == Connection.AUTH_OAUTH2:
            self._apply_oauth2()
        return self.session

    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.connection.base_url.rstrip("/") + "/" + path.lstrip("/")

    def _record_call(self, method, url, kwargs, response, error, duration_ms):
        """Best-effort — a logging bug must never take down a real API call,
        so any failure here is swallowed rather than propagated."""
        try:
            if response is not None:
                prepared = response.request
                request_headers = dict(prepared.headers)
                request_body = prepared.body
                status_code = response.status_code
                response_headers = dict(response.headers)
                response_body = response.text
            else:
                request_headers = dict(self.session.headers)
                request_body = kwargs.get("data")
                if kwargs.get("json") is not None:
                    request_body = json.dumps(kwargs["json"])
                status_code = None
                response_headers = {}
                response_body = ""

            ApiCallLog.objects.create(
                connection=self.connection, run=self.run, method=method, url=url,
                request_headers=redact_headers(request_headers), request_body=truncate_body(request_body),
                status_code=status_code, response_headers=redact_headers(response_headers),
                response_body=truncate_body(response_body), error=error, duration_ms=duration_ms,
            )
        except Exception:
            pass

    def _send(self, method: str, url: str, **kwargs):
        """The actual HTTP call + ApiCallLog capture, with no auth prep of its
        own — called by request() (after _prepare()) and directly by
        _refresh_oauth2 (which must NOT go through _prepare(), or an OAuth2
        connection would recurse: _prepare -> _apply_oauth2 ->
        refresh_oauth2_token -> _refresh_oauth2 -> _prepare -> ...)."""
        timeout = kwargs.pop("timeout", 30)
        start = time.monotonic()
        try:
            response = self.session.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            self._record_call(method, url, kwargs, response=None, error=str(exc), duration_ms=(time.monotonic() - start) * 1000)
            raise
        self._record_call(method, url, kwargs, response=response, error="", duration_ms=(time.monotonic() - start) * 1000)
        return response

    def request(self, method: str, path: str, **kwargs):
        self._prepare()
        return self._send(method, self._build_url(path), **kwargs)

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs):
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("DELETE", path, **kwargs)
