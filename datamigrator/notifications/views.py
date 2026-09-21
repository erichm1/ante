from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Notification

FEED_SIZE = 12


def _mine(request):
    return Notification.objects.filter(recipient=request.user)


def _row(n):
    return {
        "id": n.pk, "kind": n.kind, "outcome": n.outcome, "level": n.level, "title": n.title, "message": n.message,
        "url": f"/notifications/{n.pk}/go/", "read": n.read_at is not None, "created_at": n.created_at.isoformat(),
    }


@login_required
@require_GET
def feed(request):
    """What the navbar bell polls: the unread count and the latest few notifications."""
    mine = _mine(request)
    return JsonResponse({"unread": mine.filter(read_at__isnull=True).count(), "items": [_row(n) for n in mine[:FEED_SIZE]]})


@login_required
@require_POST
def mark_read(request):
    """Mark every notification of the caller's read (the bell's "Mark all read")."""
    updated = _mine(request).filter(read_at__isnull=True).update(read_at=timezone.now())
    return JsonResponse({"updated": updated})


@login_required
def go(request, pk):
    """Mark one read and go to what it is about (falls back to the list if it has no target)."""
    n = get_object_or_404(_mine(request), pk=pk)
    if n.read_at is None:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    return redirect(n.url or "notifications:list")


@login_required
def notification_list(request):
    outcome = request.GET.get("outcome", "")
    kind = request.GET.get("kind", "")
    only_unread = request.GET.get("unread") == "1"
    rows = _mine(request)
    if outcome in dict(Notification.OUTCOME_CHOICES):
        rows = rows.filter(outcome=outcome)
    if kind in dict(Notification.KIND_CHOICES):
        rows = rows.filter(kind=kind)
    if only_unread:
        rows = rows.filter(read_at__isnull=True)
    page = Paginator(rows, 25).get_page(request.GET.get("page"))
    return render(request, "notifications/list.html", {
        "page": page, "outcomes": Notification.OUTCOME_CHOICES, "kinds": Notification.KIND_CHOICES,
        "filters": {"outcome": outcome, "kind": kind, "unread": only_unread},
        "unread_total": _mine(request).filter(read_at__isnull=True).count(),
    })
