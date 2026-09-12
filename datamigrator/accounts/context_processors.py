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
