from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, resolve_url

from . import permissions

OPEN_PATH_PREFIXES = ("/accounts/", "/admin/", "/static/")
# Exact paths (not prefixes) open to everybody, on top of "/" itself — a status page is only useful to someone
# who can't sign in, so it can't sit behind the same login wall as the rest of the app. Its own JSON feed
# (/home/status/data/) stays behind login: it isn't a prefix match here, so it's unaffected.
OPEN_PATHS = ("/", "/home/status/")


class LoginRequiredMiddleware:
    """Gates the whole app behind login — this is a demo/scaffold with no
    prior auth (see README's "Known scaffold limitations"), so rather than
    decorate every page view individually, anonymous requests are redirected
    to LOGIN_URL from one place. /api/ is exempted from the HTML redirect
    (a redirect would hand a fetch() caller an HTML login page where it
    expects JSON) and gets a plain 401 instead — in normal use this never
    fires, since every page that calls the API already required login to
    reach. The pages open to everybody are "/" (the public landing page) and
    "/home/status/" (the status page — see OPEN_PATHS)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and request.path not in OPEN_PATHS and not request.path.startswith(OPEN_PATH_PREFIXES):
            if request.path.startswith("/api/"):
                return JsonResponse({"detail": "Authentication required."}, status=401)
            return redirect(f"{resolve_url(settings.LOGIN_URL)}?next={request.path}")
        return self.get_response(request)


# JSON endpoints that live outside /api/ (the Studio's own) — they get a 403 body, not a redirect.
_JSON_PATHS = ("/api/", "/studio/tree/", "/studio/templates/")


class ModuleAccessMiddleware:
    """Enforces accounts.permissions on every request, after login. Someone without the module a URL belongs to
    gets a clear warning instead of the feature: a page redirects home with a warning message (the navbar
    shows it), the JSON API answers 403 with {"error": …, "permission_denied": true} — which the Studio turns
    into a warning toast. Administrators pass everything; URLs outside any module (home, profile,
    notifications) are open to every signed-in user."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            path = request.path
            if path.startswith(permissions.ADMIN_ONLY_PREFIXES):
                if not permissions.is_admin(user):
                    return self._deny(request, "Only administrators can manage users, groups and departments.", ["admin"])
            else:
                keys = permissions.modules_for_path(path)
                if keys is not None and not any(permissions.has_module(user, k) for k in keys):
                    return self._deny(request, permissions.denial_message(keys), list(keys))
        return self.get_response(request)

    @staticmethod
    def _deny(request, message, keys):
        if request.path.startswith(_JSON_PATHS):
            return JsonResponse({"error": message, "permission_denied": True, "modules": keys}, status=403)
        messages.warning(request, message)
        return redirect("home:index")
