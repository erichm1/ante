import json
import time
from urllib.parse import urlencode

import requests
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from connections.models import Connection

from .models import Integration, InstalledIntegration, OAuthPendingConnection


def _parse_kv_json(raw) -> dict:
    """The install form's custom-headers/custom-params hidden inputs are
    filled in by JS (see install.html's prepareInstallSubmit) as a JSON
    object — malformed/missing input just means "none set" rather than a
    failed install."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _oauth_redirect_uri(request) -> str:
    """The single, provider-agnostic redirect/callback URI every OAuth2
    Integration shares — this is what an admin registers in the provider's
    own OAuth app settings (see IntegrationAdmin's matching display field)."""
    return request.build_absolute_uri(reverse("integrations:oauth_callback"))


def _unique_connection_name(base_name: str) -> str:
    """Connection.name is unique, but installing the same Integration twice
    (e.g. two Shopify stores) defaults both to the integration's own name
    unless the installer types something different — auto-suffix rather
    than let the second install crash with an IntegrityError. This also
    covers the OAuth path, where by the time the provider redirects back
    there's no form left to correct."""
    name = base_name
    suffix = 2
    while Connection.objects.filter(name=name).exists():
        name = f"{base_name} ({suffix})"
        suffix += 1
    return name


def app_store_list(request):
    tab = request.GET.get("tab", "catalog")
    if tab not in ("catalog", "installed"):
        tab = "catalog"

    if tab == "installed":
        connections = Connection.objects.all().select_related(
            "integration_install", "integration_install__integration",
        ).order_by("name")
        return render(request, "integrations/list.html", {
            "tab": tab, "connections": connections, "auth_types": Connection.AUTH_TYPE_CHOICES,
        })

    category = request.GET.get("category", "").strip()
    name_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()  # "installed" | "not_installed" | ""

    integrations = Integration.objects.filter(is_active=True).prefetch_related("installs__connection")
    if category in dict(Integration.Category.choices):
        integrations = integrations.filter(category=category)
    if name_query:
        integrations = integrations.filter(name__icontains=name_query)

    filtered = []
    for integration in integrations:
        # A catalog entry can be installed more than once (e.g. two separate
        # Shopify stores) — each install is its own Connection, so this is a
        # list, not a single "the" installed connection.
        integration.installed_connections = [i.connection for i in integration.installs.all() if i.is_active]
        is_installed = bool(integration.installed_connections)
        if status_filter == "installed" and not is_installed:
            continue
        if status_filter == "not_installed" and is_installed:
            continue
        filtered.append(integration)

    return render(request, "integrations/list.html", {
        "tab": tab,
        "integrations": filtered,
        "categories": Integration.Category.choices,
        "filters": {"category": category, "q": name_query, "status": status_filter},
    })


def integration_install(request, pk):
    integration = get_object_or_404(Integration, pk=pk, is_active=True)

    if request.method == "POST":
        connection_name = request.POST.get("connection_name", "").strip() or integration.name

        if integration.uses_oauth_redirect:
            pending = OAuthPendingConnection.objects.create(integration=integration, connection_name=connection_name)
            params = {
                "response_type": "code",
                "client_id": integration.oauth_client_id,
                "redirect_uri": _oauth_redirect_uri(request),
                "scope": integration.oauth_scope,
                "state": pending.state,
            }
            if not integration.oauth_authorize_url:
                messages.error(request, "This integration has no oauth_authorize_url configured yet — set one in the admin.")
                pending.delete()
                return redirect("integrations:install", pk=pk)
            return redirect(f"{integration.oauth_authorize_url}?{urlencode(params)}")

        base_url = "" if integration.is_file_based else (request.POST.get("base_url", "").strip() or integration.default_base_url)
        connection = Connection.objects.create(
            name=_unique_connection_name(connection_name), base_url=base_url, auth_type=integration.auth_type,
            use_custom_headers=bool(request.POST.get("install_use_custom_headers")),
            custom_headers=_parse_kv_json(request.POST.get("custom_headers")),
            use_custom_params=bool(request.POST.get("install_use_custom_params")),
            custom_params=_parse_kv_json(request.POST.get("custom_params")),
        )

        if integration.auth_type == Connection.AUTH_BASIC:
            connection.merge_secrets({
                "username": request.POST.get("username", ""),
                "password": request.POST.get("password", ""),
            })
        elif integration.auth_type == Connection.AUTH_BEARER:
            connection.merge_secrets({"token": request.POST.get("token", "")})
        elif integration.auth_type == Connection.AUTH_JWT:
            connection.auth_config = {
                "claims": {"sub": connection_name},
                "ttl_seconds": 3600,
            }
            connection.merge_secrets({"signing_secret": request.POST.get("signing_secret", "")})
        connection.save()

        InstalledIntegration.objects.create(integration=integration, connection=connection)
        messages.success(request, f"Installed {integration.name}.")
        return redirect("connections:detail", pk=connection.pk)

    return render(request, "integrations/install.html", {
        "integration": integration, "oauth_redirect_uri": _oauth_redirect_uri(request),
    })


def reconnect_connection(request, pk):
    """Re-authorize an existing OAuth2 connection that was installed from the
    App Store. Deliberately reuses the exact same shared callback URL
    (_oauth_redirect_uri) as installing it the first time, rather than
    connections:oauth_authorize's own per-connection URL — a provider only
    has one redirect URI on file (see IntegrationAdmin's "OAuth2 redirect
    URL" field), so reconnecting through a different URL than the one that
    was actually registered fails at the provider with a redirect_uri
    mismatch. Connections not installed from the App Store (hand-configured,
    with their own auth_config) have no Integration to pull client_id/
    authorize_url from, so they keep using connections:oauth_authorize."""
    connection = get_object_or_404(Connection, pk=pk, auth_type=Connection.AUTH_OAUTH2)
    install = getattr(connection, "integration_install", None)
    if not install:
        messages.error(request, "This connection wasn't installed from the App Store — use its own Reconnect flow instead.")
        return redirect("connections:detail", pk=pk)

    integration = install.integration
    if not integration.oauth_authorize_url:
        messages.error(request, "This integration has no oauth_authorize_url configured yet — set one in the admin.")
        return redirect("connections:detail", pk=pk)

    pending = OAuthPendingConnection.objects.create(
        integration=integration, connection_name=connection.name, reconnect_connection=connection,
    )
    params = {
        "response_type": "code",
        "client_id": integration.oauth_client_id,
        "redirect_uri": _oauth_redirect_uri(request),
        "scope": integration.oauth_scope,
        "state": pending.state,
    }
    return redirect(f"{integration.oauth_authorize_url}?{urlencode(params)}")


def oauth_callback(request):
    state = request.GET.get("state")
    pending = OAuthPendingConnection.objects.filter(state=state).select_related(
        "integration", "reconnect_connection",
    ).first()
    if not pending:
        messages.error(request, "OAuth state mismatch or expired install attempt — please retry from the App Store.")
        return redirect("integrations:list")

    integration = pending.integration
    # Where to send the user back to if anything below fails — the install
    # form for a fresh install, or straight back to the connection for a reconnect.
    retry_target = (
        redirect("connections:detail", pk=pending.reconnect_connection_id) if pending.reconnect_connection_id
        else redirect("integrations:install", pk=integration.pk)
    )

    code = request.GET.get("code")
    if not code:
        messages.error(request, f"Authorization failed: {request.GET.get('error', 'no code returned')}")
        pending.delete()
        return retry_target

    token_resp = requests.post(
        integration.oauth_token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _oauth_redirect_uri(request),
            "client_id": integration.oauth_client_id,
            "client_secret": integration.oauth_client_secret,
        },
        timeout=30,
    )
    if token_resp.status_code != 200:
        messages.error(request, f"Token exchange failed ({token_resp.status_code}): {token_resp.text[:200]}")
        pending.delete()
        return retry_target

    payload = token_resp.json()
    new_secrets = {
        "access_token": payload.get("access_token"),
        "refresh_token": payload.get("refresh_token"),
        "expires_at": time.time() + payload.get("expires_in", 3600),
    }

    if pending.reconnect_connection_id:
        connection = pending.reconnect_connection
        connection.secrets = new_secrets
        connection.save()
        pending.delete()
        messages.success(request, f"Reconnected to {integration.name}.")
        return redirect("connections:detail", pk=connection.pk)

    connection = Connection.objects.create(
        name=_unique_connection_name(pending.connection_name),
        base_url=integration.default_base_url,
        auth_type=Connection.AUTH_OAUTH2,
        auth_config={
            "client_id": integration.oauth_client_id,
            "client_secret": integration.oauth_client_secret,
            "authorize_url": integration.oauth_authorize_url,
            "token_url": integration.oauth_token_url,
            "scope": integration.oauth_scope,
        },
    )
    connection.secrets = new_secrets
    connection.save()

    InstalledIntegration.objects.create(integration=integration, connection=connection)
    pending.delete()
    messages.success(request, f"Installed {integration.name}.")
    return redirect("connections:detail", pk=connection.pk)


@require_POST
def integration_uninstall(request, pk):
    install = get_object_or_404(InstalledIntegration, pk=pk)
    integration_name = install.integration.name
    install.connection.delete()  # cascades to the InstalledIntegration
    messages.success(request, f"Uninstalled {integration_name}.")
    return redirect("integrations:list")
