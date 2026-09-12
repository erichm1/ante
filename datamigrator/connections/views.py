import json
import secrets as pysecrets
import time
from urllib.parse import urlencode

import requests
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from . import scheduler
from .client import ConnectionClient
from .log_query import filter_logs
from .models import ApiCallLog, Connection, TokenRefreshJob
from .serializers import ConnectionSecretsSerializer, ConnectionSerializer, TokenRefreshJobSerializer


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


class TokenRefreshJobViewSet(viewsets.ModelViewSet):
    """Full CRUD for the on/off, scheduled OAuth2 refresh job: create one for
    a connection, read its last-run status, update its interval or flip
    is_enabled, or delete it outright to stop proactive refreshing (the
    connection still refreshes lazily on-demand via ConnectionClient either
    way — this only controls the *proactive*, off-a-real-request background
    refresh)."""

    queryset = TokenRefreshJob.objects.select_related("connection")
    serializer_class = TokenRefreshJobSerializer
    filterset_fields = ["connection"]

    def perform_create(self, serializer):
        job = serializer.save()
        job.schedule_next_run()
        job.save(update_fields=["next_run_at"])

    def perform_update(self, serializer):
        was_enabled = serializer.instance.is_enabled
        job = serializer.save()
        # Re-enabling (or just creating) shouldn't wait out a stale interval
        # before the first refresh actually happens.
        if job.is_enabled and (not was_enabled or "interval_minutes" in serializer.validated_data):
            job.schedule_next_run()
            job.save(update_fields=["next_run_at"])

    @action(detail=True, methods=["post"], url_path="run")
    def run_now(self, request, pk=None):
        job = self.get_object()
        scheduler.run_job_now(job)
        return Response(TokenRefreshJobSerializer(job).data)


# ---- Page views -----------------------------------------------------------

def connection_list(request):
    # Moved to the App Store's "Installed" tab (integrations:list?tab=installed) —
    # kept as a redirect so old bookmarks/links still land somewhere useful.
    return redirect("/app-store/?tab=installed")


def connection_detail(request, pk):
    connection = get_object_or_404(Connection, pk=pk)
    claims_json = json.dumps((connection.auth_config or {}).get("claims", {}))
    install = getattr(connection, "integration_install", None)

    # Local import — jobs/views.py doesn't import connections, so this stays
    # one-directional and avoids a module-load cycle.
    from jobs.models import MigrationRun
    from jobs.views import _route_info

    recent_runs = list(
        MigrationRun.objects.filter(
            Q(mapping__source_connection=connection) | Q(mapping__destination_connections=connection)
        ).distinct().select_related(
            "mapping", "mapping__source_connection", "mapping__source_connection__integration_install__integration",
        ).prefetch_related(
            "mapping__destination_connections__integration_install__integration",
            "mapping__entity_mappings__source_entity", "mapping__entity_mappings__target_entity",
        ).order_by("-started_at")[:15]
    )
    for run in recent_runs:
        run.route = _route_info(run.mapping)

    return render(request, "connections/detail.html", {
        "connection": connection, "claims_json": claims_json, "auth_types": Connection.AUTH_TYPE_CHOICES,
        # OAuth2 connections installed from the App Store reconnect through its
        # single shared callback URL (integrations:reconnect) instead of this
        # app's own per-connection one — see integrations.views.reconnect_connection.
        "installed_via_app_store": install is not None,
        "refresh_job": getattr(connection, "token_refresh_job", None),
        # No real API behind this connection at all (the built-in CSV/XLSX apps) —
        # hides the API-oriented discovery tabs, which would just error out.
        "is_file_based": bool(install and install.integration.is_file_based),
        "integration": install.integration if install else None,
        "recent_runs": recent_runs,
    })


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
def save_bearer_token(request, pk):
    connection = get_object_or_404(Connection, pk=pk)
    connection.merge_secrets({"token": request.POST.get("token", "")})
    connection.save(update_fields=["secrets_encrypted"])
    messages.success(request, f"Bearer token saved for {connection.name}.")
    return redirect("connections:detail", pk=pk)


@require_POST
def save_jwt_config(request, pk):
    connection = get_object_or_404(Connection, pk=pk)
    claims_raw = request.POST.get("claims", "").strip()
    try:
        claims = json.loads(claims_raw) if claims_raw else {}
    except ValueError:
        messages.error(request, "Claims must be valid JSON.")
        return redirect("connections:detail", pk=pk)

    ttl_raw = request.POST.get("ttl_seconds", "").strip()
    config = dict(connection.auth_config or {})
    config["claims"] = claims
    config["ttl_seconds"] = int(ttl_raw) if ttl_raw.isdigit() else 3600
    connection.auth_config = config
    connection.merge_secrets({"signing_secret": request.POST.get("signing_secret", "")})
    # Force the next call to re-sign rather than reuse a token minted under the old claims/secret.
    remaining_secrets = connection.secrets
    remaining_secrets.pop("jwt", None)
    remaining_secrets.pop("expires_at", None)
    connection.secrets = remaining_secrets
    connection.save(update_fields=["auth_config", "secrets_encrypted"])
    messages.success(request, f"JWT signing config saved for {connection.name}.")
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


LOG_PAGE_SIZE_CHOICES = (10, 25, 50, 100)
LOG_DEFAULT_PAGE_SIZE = 10


def api_log_list(request):
    """Grafana/Loki-style single-query-bar log browser over every outbound
    API call the app has made (see ApiCallLog, populated by every
    ConnectionClient request — reads, writes, OAuth2 refreshes, chain steps,
    all of it). See connections/log_query.py for the query syntax."""
    # Local import: connections -> home would be a cycle at module load time
    # (home.views imports connections.models), fine as a call-time import.
    from home.views import _compute_status_data

    query = request.GET.get("q", "").strip()
    base_qs = ApiCallLog.objects.select_related("connection", "run")
    results, error = filter_logs(base_qs, query)

    try:
        page_size = int(request.GET.get("page_size", LOG_DEFAULT_PAGE_SIZE))
    except ValueError:
        page_size = LOG_DEFAULT_PAGE_SIZE
    if page_size not in LOG_PAGE_SIZE_CHOICES:
        page_size = LOG_DEFAULT_PAGE_SIZE

    page_obj = Paginator(results, page_size).get_page(request.GET.get("page")) if error is None else None

    status_data = _compute_status_data()
    api_error_reason = next((r for r in status_data["reasons"] if r["kind"] == "api_errors"), None)

    return render(request, "connections/logs.html", {
        "query": query,
        "error": error,
        "page_obj": page_obj,
        "page_size": page_size,
        "page_size_choices": LOG_PAGE_SIZE_CHOICES,
        "overall_status": status_data["overall"],
        "api_error_reason": api_error_reason,
        "viewing_errors": "error:true" in query,
    })
