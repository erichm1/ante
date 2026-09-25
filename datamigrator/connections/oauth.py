"""How Ante talks to an OAuth2 provider's token endpoint, shared by every place that does it: the authorization-code
exchange (connections/views.py, integrations/views.py) and the refresh_token grant (connections/client.py).

Providers differ in two ways that matter here, both set in a connection's `auth_config`:

  client_auth    "body" (default) sends client_id / client_secret as form fields; "basic" sends them as an
                 `Authorization: Basic base64(client_id:client_secret)` header instead — what Bling and many
                 others require.
  extra_headers  {"header": "value"} sent on every token-endpoint call AND on every API request made with the
                 resulting token. Bling, for one, only issues (and keeps renewing) JWT access tokens when
                 `enable-jwt: 1` accompanies the token calls, and expects it on the API calls too.
"""
import base64

CLIENT_AUTH_BODY = "body"
CLIENT_AUTH_BASIC = "basic"
CLIENT_AUTH_CHOICES = [
    (CLIENT_AUTH_BODY, "Form fields (client_id / client_secret in the body)"),
    (CLIENT_AUTH_BASIC, "HTTP Basic header (base64 of client_id:client_secret)"),
]


def extra_headers(config: dict) -> dict:
    """The connection's `extra_headers` with every value as a string (JSON `1` → "1"), as HTTP headers need."""
    return {str(k): str(v) for k, v in ((config or {}).get("extra_headers") or {}).items()}


def token_request(config: dict, grant: dict) -> tuple[dict, dict]:
    """(form data, headers) for a POST to the token endpoint. `grant` is the grant-specific part
    ({"grant_type": "authorization_code", "code": …} or {"grant_type": "refresh_token", "refresh_token": …});
    this adds the client credentials the way `config["client_auth"]` says, plus `extra_headers`."""
    config = config or {}
    client_id, client_secret = config.get("client_id", ""), config.get("client_secret", "")
    data, headers = dict(grant), extra_headers(config)
    if config.get("client_auth") == CLIENT_AUTH_BASIC:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    else:
        data["client_id"], data["client_secret"] = client_id, client_secret
    return data, headers
