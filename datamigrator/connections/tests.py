import base64
import time
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from integrations.models import Integration

from .client import ConnectionClient
from .models import ApiCallLog, Connection
from .oauth import token_request

BLING = {
    "client_id": "cid", "client_secret": "sec", "token_url": "https://api.bling.example/oauth/token",
    "client_auth": "basic", "extra_headers": {"enable-jwt": 1},
}


def response(status=200, payload=None):
    resp = requests.Response()
    resp.status_code = status
    resp._content = requests.compat.json.dumps(payload or {}).encode()
    resp.request = requests.Request("GET", "https://x.example").prepare()
    return resp


class TokenRequestTests(SimpleTestCase):
    """connections/oauth.py: how client credentials and extra headers go to a provider's token endpoint."""

    def test_default_sends_the_credentials_as_form_fields(self):
        data, headers = token_request({"client_id": "cid", "client_secret": "sec"}, {"grant_type": "refresh_token", "refresh_token": "r"})
        self.assertEqual(data, {"grant_type": "refresh_token", "refresh_token": "r", "client_id": "cid", "client_secret": "sec"})
        self.assertEqual(headers, {})

    def test_basic_sends_them_as_an_authorization_header_and_keeps_them_out_of_the_body(self):
        data, headers = token_request(BLING, {"grant_type": "authorization_code", "code": "c"})
        self.assertEqual(data, {"grant_type": "authorization_code", "code": "c"})
        self.assertEqual(headers["Authorization"], "Basic " + base64.b64encode(b"cid:sec").decode())

    def test_extra_headers_are_sent_as_strings(self):
        _, headers = token_request(BLING, {})
        self.assertEqual(headers["enable-jwt"], "1")


class OAuth2ClientTests(TestCase):
    def setUp(self):
        self.conn = Connection.objects.create(
            name="Bling", base_url="https://api.bling.example/Api/v3", auth_type=Connection.AUTH_OAUTH2, auth_config=BLING)
        self.conn.secrets = {"access_token": "old", "refresh_token": "ref", "expires_at": time.time() + 3600}
        self.conn.save()

    def test_api_requests_carry_the_bearer_token_and_the_extra_header(self):
        client = ConnectionClient(self.conn)
        with mock.patch("requests.Session.request", return_value=response(200)) as send:
            client.get("/produtos")
        self.assertEqual(send.call_args.args[1], "https://api.bling.example/Api/v3/produtos")
        self.assertEqual(client.session.headers["Authorization"], "Bearer old")
        self.assertEqual(client.session.headers["enable-jwt"], "1")

    def test_refresh_uses_basic_auth_and_the_extra_header(self):
        with mock.patch("requests.Session.request", return_value=response(200, {"access_token": "new", "expires_in": 21600})) as send:
            secrets = ConnectionClient(self.conn).refresh_oauth2_token(force=True)
        (method, url), kwargs = send.call_args.args, send.call_args.kwargs
        self.assertEqual((method, url), ("POST", BLING["token_url"]))
        self.assertEqual(kwargs["data"], {"grant_type": "refresh_token", "refresh_token": "ref"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Basic " + base64.b64encode(b"cid:sec").decode())
        self.assertEqual(kwargs["headers"]["enable-jwt"], "1")
        self.assertEqual(secrets["access_token"], "new")
        self.assertEqual(self.conn.secrets["refresh_token"], "ref")           # kept when the provider doesn't send a new one

    def test_a_401_renews_the_token_and_retries_once(self):
        answers = [response(401), response(200, {"access_token": "new", "expires_in": 21600}), response(200, {"ok": True})]
        with mock.patch("requests.Session.request", side_effect=answers) as send:
            client = ConnectionClient(self.conn)
            result = client.get("/produtos")
        self.assertEqual(result.status_code, 200)
        self.assertEqual([c.args[1] for c in send.call_args_list],
                         ["https://api.bling.example/Api/v3/produtos", BLING["token_url"], "https://api.bling.example/Api/v3/produtos"])
        self.assertEqual(client.session.headers["Authorization"], "Bearer new")
        self.assertEqual(ApiCallLog.objects.count(), 3)                        # all three calls are in the log

    def test_a_401_is_returned_as_is_when_the_renewal_fails(self):
        answers = [response(401), response(400, {"error": "invalid_grant"})]
        with mock.patch("requests.Session.request", side_effect=answers) as send:
            result = ConnectionClient(self.conn).get("/produtos")
        self.assertEqual(result.status_code, 401)
        self.assertEqual(send.call_count, 2)                                   # no endless retry loop

    def test_a_401_without_a_refresh_token_is_not_retried(self):
        self.conn.secrets = {"access_token": "old", "expires_at": time.time() + 3600}
        self.conn.save()
        with mock.patch("requests.Session.request", return_value=response(401)) as send:
            self.assertEqual(ConnectionClient(self.conn).get("/produtos").status_code, 401)
        self.assertEqual(send.call_count, 1)

    def test_other_auth_types_do_not_retry_a_401(self):
        bearer = Connection.objects.create(name="B", base_url="https://b.example", auth_type=Connection.AUTH_BEARER)
        bearer.secrets = {"token": "t"}
        with mock.patch("requests.Session.request", return_value=response(401)) as send:
            self.assertEqual(ConnectionClient(bearer).get("/x").status_code, 401)
        self.assertEqual(send.call_count, 1)


class InstalledIntegrationTests(TestCase):
    def test_the_integration_hands_its_token_settings_to_the_connection_it_creates(self):
        plain = Integration(name="Plain", slug="plain", auth_type="oauth2", oauth_token_url="https://p.example/token")
        self.assertNotIn("client_auth", plain.oauth_auth_config())
        self.assertNotIn("extra_headers", plain.oauth_auth_config())
        bling = Integration(name="Bling", slug="bling", auth_type="oauth2", oauth_client_auth="basic", oauth_extra_headers={"enable-jwt": "1"})
        self.assertEqual(bling.oauth_auth_config()["client_auth"], "basic")
        self.assertEqual(bling.oauth_auth_config()["extra_headers"], {"enable-jwt": "1"})

    def test_the_callback_exchanges_the_code_with_basic_auth_and_the_header(self):
        from integrations.models import OAuthPendingConnection

        self.client.force_login(get_user_model().objects.create_user("ada", password="pw"))
        bling = Integration.objects.create(
            name="Bling", slug="bling", auth_type="oauth2", oauth_authorize_url="https://b.example/auth", oauth_token_url="https://b.example/token",
            oauth_client_id="cid", oauth_client_secret="sec", oauth_client_auth="basic", oauth_extra_headers={"enable-jwt": "1"},
            default_base_url="https://b.example/api")
        pending = OAuthPendingConnection.objects.create(integration=bling, connection_name="My Bling")
        token = mock.Mock(status_code=200, **{"json.return_value": {"access_token": "jwt.jwt.jwt", "refresh_token": "r", "expires_in": 21600}})
        with mock.patch("integrations.views.requests.post", return_value=token) as post:
            self.client.get("/app-store/oauth/callback/", {"state": pending.state, "code": "abc"})
        self.assertEqual(post.call_args.kwargs["data"]["code"], "abc")
        self.assertNotIn("client_secret", post.call_args.kwargs["data"])
        self.assertEqual(post.call_args.kwargs["headers"]["enable-jwt"], "1")
        self.assertTrue(post.call_args.kwargs["headers"]["Authorization"].startswith("Basic "))
        conn = Connection.objects.get(name="My Bling")
        self.assertEqual(conn.auth_config["extra_headers"], {"enable-jwt": "1"})
        self.assertEqual(conn.secrets["access_token"], "jwt.jwt.jwt")
