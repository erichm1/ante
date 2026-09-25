from .views import nav_status_level


def nav_status(request):
    """The navbar's Status dot on first paint — static/js/home_nav_status.js polls
    home:nav_status afterwards to keep it live without a reload."""
    if not request.user.is_authenticated:
        return {}
    return {"nav_status_level": nav_status_level()}
