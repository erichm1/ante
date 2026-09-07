from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, resolve_url

OPEN_PATH_PREFIXES = ("/accounts/", "/admin/", "/static/")


class LoginRequiredMiddleware:
    """Gates the whole app behind login — this is a demo/scaffold with no
    prior auth (see README's "Known scaffold limitations"), so rather than
    decorate every page view individually, anonymous requests are redirected
    to LOGIN_URL from one place. /api/ is exempted from the HTML redirect
    (a redirect would hand a fetch() caller an HTML login page where it
    expects JSON) and gets a plain 401 instead — in normal use this never
    fires, since every page that calls the API already required login to
    reach."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not request.path.startswith(OPEN_PATH_PREFIXES):
            if request.path.startswith("/api/"):
                return JsonResponse({"detail": "Authentication required."}, status=401)
            return redirect(f"{resolve_url(settings.LOGIN_URL)}?next={request.path}")
        return self.get_response(request)
