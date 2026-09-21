"""Who may use which part of the app.

The app is split into MODULES, each simply on or off for a person. A user's access is:

    administrators (is_staff / is_superuser)  →  every module, always
    everyone else                             →  (modules of all their AccessGroups
                                                   + Profile.allowed_modules)
                                                   − Profile.denied_modules

`accounts.middleware.ModuleAccessMiddleware` enforces it on every request — pages get a warning message and a
redirect home, the JSON API gets a 403 — and the navbar / Studio read the same answer to show what is locked.
"""
from dataclasses import dataclass

from django.contrib.auth import get_user_model


@dataclass(frozen=True)
class Module:
    key: str
    label: str
    icon: str
    description: str


MODULES = (
    Module("status", "Status", "bi-activity", "Platform status: connection health and API call volumes."),
    Module("appstore", "App Store & connections", "bi-shop", "Install integrations and manage connections and their entities."),
    Module("mappings", "Mappings", "bi-diagram-2", "Create and edit mappings: entity pairs, field wiring and transforms."),
    Module("jobs", "Jobs (runs)", "bi-play-circle", "Start, watch, retry and kill migration runs."),
    Module("plans", "Plans", "bi-list-ol", "Build, schedule, execute and kill plans and custom runs."),
    Module("chains", "Chains", "bi-link-45deg", "Script and run multi-step API call chains."),
    Module("studio", "Studio", "bi-window-desktop", "The visual workspace for mappings, chains, plans and runs."),
    Module("reports", "Reports", "bi-file-earmark-bar-graph", "Build and export cross-entity reports."),
    Module("tickets", "Tickets", "bi-ticket-perforated", "Open and follow support tickets."),
    Module("incidents", "Incidents", "bi-exclamation-triangle", "Track outages and issues, with a timeline and post-mortems."),
    Module("logs", "Logs", "bi-journal-text", "The history of every outbound API call."),
)
MODULE_BY_KEY = {m.key: m for m in MODULES}
ALL_KEYS = frozenset(MODULE_BY_KEY)

# What the built-in "Standard user" group switches on: everything an ordinary user could already reach
# before permissions existed (incidents was staff-only).
STANDARD_GROUP_MODULES = [m.key for m in MODULES if m.key != "incidents"]

# URL prefix → the module(s) that unlock it (ANY of them is enough). Longest prefix wins, so /connections/logs/
# is Logs while the rest of /connections/ is the App Store. Anything not listed (home, profile, notifications,
# login, static/media) is open to every signed-in user.
_ENTITY_USERS = ("appstore", "mappings", "studio", "chains", "reports")     # entities/fields are read by all of these
RULES = {
    "/home/status/": ("status",),
    "/connections/logs/": ("logs",), "/connections/": ("appstore",), "/app-store/": ("appstore",),
    "/mappings/": ("mappings",), "/jobs/": ("jobs",), "/plans/": ("plans",), "/chains/": ("chains",),
    "/studio/": ("studio",), "/reports/": ("reports",), "/tickets/": ("tickets",), "/incidents/": ("incidents",),
    "/api/connections/": ("appstore",), "/api/token-refresh-jobs/": ("appstore",),
    "/api/entities/": _ENTITY_USERS, "/api/fields/": _ENTITY_USERS,
    "/api/mappings/": ("mappings",), "/api/entity-mappings/": ("mappings",), "/api/field-mappings/": ("mappings",),
    "/api/runs/": ("jobs",), "/api/plans/": ("plans",), "/api/plan-steps/": ("plans",),
    "/api/chains/": ("chains",), "/api/chain-steps/": ("chains",),
    "/api/reports/": ("reports",), "/api/report-sections/": ("reports",),
    "/api/tickets/": ("tickets",), "/api/ticket-notes/": ("tickets",),
    "/api/incidents/": ("incidents",), "/api/incident-notes/": ("incidents",),
    "/api/attachments/": ("tickets", "incidents"),
}
_RULES_BY_LENGTH = sorted(RULES.items(), key=lambda kv: len(kv[0]), reverse=True)

# The user-administration API: administrators only, whatever their modules.
ADMIN_ONLY_PREFIXES = ("/api/admin-users/", "/api/admin-groups/", "/api/admin-departments/", "/api/admin-modules/")


def modules_for_path(path):
    """The module keys, any one of which unlocks `path` — or None when the path is open to every signed-in user."""
    for prefix, keys in _RULES_BY_LENGTH:
        if path.startswith(prefix):
            return keys
    return None


def is_admin(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def effective_modules(user) -> frozenset:
    """Every module this user may use. Memoised on the user object for the life of the request."""
    if not user or not user.is_authenticated:
        return frozenset()
    cached = getattr(user, "_ante_modules", None)
    if cached is not None:
        return cached
    if is_admin(user):
        modules = ALL_KEYS
    else:
        from .models import Profile
        profile, _ = Profile.objects.get_or_create(user=user)
        granted = set(profile.allowed_modules or [])
        for group in profile.groups.all():
            granted |= set(group.modules or [])
        modules = frozenset((granted - set(profile.denied_modules or [])) & ALL_KEYS)
    user._ante_modules = modules
    return modules


def has_module(user, key: str) -> bool:
    return key in effective_modules(user)


def users_with_module(key: str):
    """Active users who may use a module — who a notification about it should go to."""
    User = get_user_model()
    return [u for u in User.objects.filter(is_active=True) if has_module(u, key)]


def denial_message(keys) -> str:
    labels = " or ".join(MODULE_BY_KEY[k].label for k in keys if k in MODULE_BY_KEY) or "this area"
    return f"You don't have permission to use {labels}. Ask an administrator to enable it for your account."
