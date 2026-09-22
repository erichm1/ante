from functools import wraps

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts import permissions as access
from connections.models import Connection

from .models import Incident, IncidentNote, PostMortem, IncidentRule, SERVICE_CHOICES
from .serializers import IncidentNoteSerializer, IncidentSerializer, IncidentRuleSerializer, PostMortemSerializer


class IsStaffPermission(permissions.BasePermission):
    """Incidents used to be hard-wired to is_staff; it is now the "incidents" module like any other (administrators
    have it automatically, and anyone else can be granted it). Kept under the old name for the imports."""

    def has_permission(self, request, view):
        return access.has_module(request.user, "incidents")


def staff_member_required(view):
    """Page-level twin of IsStaffPermission. ModuleAccessMiddleware already turns a missing module into a warning +
    redirect before a view runs; this is the belt to that braces (and covers the view being called directly)."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not access.has_module(request.user, "incidents"):
            from django.contrib import messages
            from django.shortcuts import redirect
            messages.warning(request, access.denial_message(["incidents"]))
            return redirect("home:index")
        return view(request, *args, **kwargs)
    return wrapped


class IncidentViewSet(viewsets.ModelViewSet):
    queryset = Incident.objects.select_related("connection")
    serializer_class = IncidentSerializer
    permission_classes = [IsStaffPermission]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    @action(detail=True, methods=["post"], url_path="notes")
    def add_note(self, request, pk=None):
        incident = self.get_object()
        body = (request.data.get("body") or "").strip()
        if not body:
            return Response({"error": "Note body is required."}, status=status.HTTP_400_BAD_REQUEST)
        note = IncidentNote.objects.create(incident=incident, body=body)
        return Response(IncidentNoteSerializer(note).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve(self, request, pk=None):
        incident = self.get_object()
        incident.status = Incident.STATUS_RESOLVED
        incident.resolved_at = timezone.now()
        incident.save()
        return Response(IncidentSerializer(incident).data)

    @action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        incident = self.get_object()
        incident.status = Incident.STATUS_OPEN
        incident.resolved_at = None
        incident.save()
        return Response(IncidentSerializer(incident).data)

    @action(detail=True, methods=["post"], url_path="close-tickets")
    def close_tickets(self, request, pk=None):
        incident = self.get_object()
        now = timezone.now()
        updated = incident.tickets.exclude(
            status__in=["resolved", "closed"]
        ).update(status="closed", resolved_at=now)
        return Response({"closed": updated})


class IncidentNoteViewSet(viewsets.ModelViewSet):
    queryset = IncidentNote.objects.select_related("incident")
    serializer_class = IncidentNoteSerializer
    permission_classes = [IsStaffPermission]
    http_method_names = ["get", "patch", "delete", "head", "options"]


# ── Page views ──────────────────────────────────────────────────────────────

PAGE_SIZE_CHOICES = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 25


@staff_member_required
def incident_list(request):
    incidents = Incident.objects.select_related("connection").prefetch_related("postmortem")

    q               = request.GET.get("q", "").strip()
    status_filter   = request.GET.get("status", "").strip()
    severity_filter = request.GET.get("severity", "").strip()
    if q:
        incidents = incidents.filter(title__icontains=q)
    if status_filter in dict(Incident.STATUS_CHOICES):
        incidents = incidents.filter(status=status_filter)
    if severity_filter in dict(Incident.SEVERITY_CHOICES):
        incidents = incidents.filter(severity=severity_filter)

    try:
        page_size = int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    if page_size not in PAGE_SIZE_CHOICES:
        page_size = DEFAULT_PAGE_SIZE

    page_obj = Paginator(incidents, page_size).get_page(request.GET.get("page"))

    return render(request, "incidents/list.html", {
        "page_obj":          page_obj,
        "filters":           {"q": q, "status": status_filter, "severity": severity_filter},
        "status_choices":    Incident.STATUS_CHOICES,
        "severity_choices":  Incident.SEVERITY_CHOICES,
        "trigger_choices":   IncidentRule.TRIGGER_CHOICES,
        "connections":       Connection.objects.order_by("name"),
        "page_size":         page_size,
        "page_size_choices": PAGE_SIZE_CHOICES,
    })


@staff_member_required
def incident_detail(request, pk):
    incident = get_object_or_404(
        Incident.objects.select_related("connection").prefetch_related("notes", "tickets__created_by", "attachments__uploaded_by"),
        pk=pk,
    )
    postmortem = getattr(incident, "postmortem", None)
    return render(request, "incidents/detail.html", {
        "incident":        incident,
        "notes":           list(incident.notes.all()),
        "tickets":         list(incident.tickets.select_related("created_by").order_by("-created_at")),
        "attachments":     list(incident.attachments.all()),
        "postmortem":      postmortem,
        "connections":     Connection.objects.order_by("name"),
        "status_choices":  Incident.STATUS_CHOICES,
        "severity_choices": Incident.SEVERITY_CHOICES,
        "service_choices": __import__("incidents.models", fromlist=["SERVICE_CHOICES"]).SERVICE_CHOICES,
    })


@staff_member_required
def incident_postmortem(request, pk):
    from .models import PostMortem
    incident = get_object_or_404(Incident, pk=pk)
    postmortem, _ = PostMortem.objects.get_or_create(
        incident=incident,
        defaults={"written_by": request.user},
    )
    if request.method == "POST":
        fields = [
            "summary", "timeline", "impact", "root_cause",
            "contributing_factors", "what_went_well", "what_went_wrong",
            "action_items", "lessons_learned",
        ]
        for f in fields:
            setattr(postmortem, f, request.POST.get(f, ""))
        postmortem.written_by = request.user
        postmortem.save()
        from django.contrib import messages
        messages.success(request, "Post-mortem saved.")
        return render(request, "incidents/postmortem.html", {"incident": incident, "pm": postmortem})
    return render(request, "incidents/postmortem.html", {"incident": incident, "pm": postmortem})
