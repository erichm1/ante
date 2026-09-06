import secrets as pysecrets
import time
from urllib.parse import urlencode

import requests
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .client import ConnectionClient
from .models import Connection
from .serializers import ConnectionSecretsSerializer, ConnectionSerializer


class ConnectionViewSet(viewsets.ModelViewSet):
    queryset = Connection.objects.all()
    serializer_class = ConnectionSerializer

    @action(detail=True, methods=["post"], url_path="secrets")
    def set_secrets(self, request, pk=None):
        connection = self.get_object()
        serializer = ConnectionSecretsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection.merge_secrets(serializer.validated_data)
        connection.save(update_fields=["secrets_encrypted"])
        return Response({"status": "saved"})

    @action(detail=True, methods=["post"], url_path="test")
    def test_connection(self, request, pk=None):
        connection = self.get_object()
        path = request.data.get("path", "/")
        try:
            resp = ConnectionClient(connection).get(path)
            return Response({"status_code": resp.status_code, "ok": resp.ok})
        except Exception as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# ---- Page views -----------------------------------------------------------

def connection_list(request):
    connections = Connection.objects.all()
    return render(request, "connections/list.html", {
        "connections": connections,
        "auth_types": Connection.AUTH_TYPE_CHOICES,
    })


def connection_detail(request, pk):
    connection = get_object_or_404(Connection, pk=pk)
    return render(request, "connections/detail.html", {"connection": connection})


@require_POST
def save_basic_secrets(request, pk):
    connection = get_object_or_404(Connection, pk=pk)
    connection.merge_secrets({
        "username": request.POST.get("username", ""),
        "password": request.POST.get("password", ""),
    })
    connection.save(update_fields=["secrets_encrypted"])
    messages.success(request, f"Credentials saved for {connection.name}.")
    return redirect("connections:detail", pk=pk)


@require_POST
def save_api_key(request, pk):
    connection = get_object_or_404(Connection, pk=pk)
    connection.merge_secrets({"api_key": request.POST.get("api_key", "")})
    connection.save(update_fields=["secrets_encrypted"])
    messages.success(request, f"API key saved for {connection.name}.")
    return redirect("connections:detail", pk=pk)


def oauth_authorize(request, pk):
    connection = get_object_or_404(Connection, pk=pk)
    config = connection.auth_config or {}
    state = pysecrets.token_urlsafe(24)
    request.session[f"oauth_state_{pk}"] = state

    params = {
        "response_type": "code",
        "client_id": config.get("client_id", ""),
        "redirect_uri": request.build_absolute_uri(reverse("connections:oauth_callback", args=[pk])),
        "scope": config.get("scope", ""),
        "state": state,
    }
    authorize_url = config.get("authorize_url", "")
    if not authorize_url:
        messages.error(request, "Set an authorize_url in this connection's auth config first.")
        return redirect("connections:detail", pk=pk)
    return redirect(f"{authorize_url}?{urlencode(params)}")


def oauth_callback(request, pk):
    connection = get_object_or_404(Connection, pk=pk)
    config = connection.auth_config or {}

    expected_state = request.session.pop(f"oauth_state_{pk}", None)
    if not expected_state or request.GET.get("state") != expected_state:
        messages.error(request, "OAuth state mismatch — please retry the connection.")
        return redirect("connections:detail", pk=pk)

    code = request.GET.get("code")
    if not code:
        messages.error(request, f"Authorization failed: {request.GET.get('error', 'no code returned')}")
        return redirect("connections:detail", pk=pk)

    token_resp = requests.post(
        config.get("token_url", ""),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": request.build_absolute_uri(reverse("connections:oauth_callback", args=[pk])),
            "client_id": config.get("client_id", ""),
            "client_secret": config.get("client_secret", ""),
        },
        timeout=30,
    )
    if token_resp.status_code != 200:
        messages.error(request, f"Token exchange failed ({token_resp.status_code}): {token_resp.text[:200]}")
        return redirect("connections:detail", pk=pk)

    payload = token_resp.json()
    connection.secrets = {
        "access_token": payload.get("access_token"),
        "refresh_token": payload.get("refresh_token"),
        "expires_at": time.time() + payload.get("expires_in", 3600),
    }
    connection.save(update_fields=["secrets_encrypted"])
    messages.success(request, f"Connected to {connection.name}.")
    return redirect("connections:detail", pk=pk)
