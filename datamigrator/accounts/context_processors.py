from django.conf import settings

from . import permissions
from .models import Profile


def profile(request):
    """Makes the logged-in user's Profile available on every page (the
    sidebar shows their avatar instead of just a username) without every
    template needing to guard against a Profile that doesn't exist yet —
    get_or_create the same way accounts/views.py::profile itself does, so
    a user who's never opened the profile page still gets a row here."""
    if not request.user.is_authenticated:
        return {}
    profile_obj, _ = Profile.objects.get_or_create(user=request.user)
    return {"current_profile": profile_obj}


def access(request):
    """What this user may use — the navbar marks locked modules, and shows the admin-only links. Anonymous
    pages only learn whether open sign-up is switched on."""
    if not request.user.is_authenticated:
        return {"allow_self_registration": settings.ALLOW_SELF_REGISTRATION}
    return {"user_modules": permissions.effective_modules(request.user), "is_app_admin": permissions.is_admin(request.user)}
