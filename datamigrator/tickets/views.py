from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from incidents.models import Incident

from .models import Ticket, TicketNote
from .serializers import TicketNoteSerializer, TicketSerializer


class TicketViewSet(viewsets.ModelViewSet):
    serializer_class = TicketSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if self.request.user.is_staff:
            return Ticket.objects.select_related("incident", "created_by")
        return Ticket.objects.filter(created_by=self.request.user).select_related("incident", "created_by")

    @action(detail=True, methods=["post"], url_path="notes")
    def add_note(self, request, pk=None):
        ticket = self.get_object()
        body = (request.data.get("body") or "").strip()
        if not body:
            return Response({"error": "Note body is required."}, status=status.HTTP_400_BAD_REQUEST)
        note = TicketNote.objects.create(ticket=ticket, body=body)
        return Response(TicketNoteSerializer(note).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve(self, request, pk=None):
        ticket = self.get_object()
        ticket.status = Ticket.STATUS_RESOLVED
        ticket.resolved_at = timezone.now()
        ticket.save()
        return Response(TicketSerializer(ticket, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        ticket = self.get_object()
        ticket.status = Ticket.STATUS_OPEN
        ticket.resolved_at = None
        ticket.save()
        return Response(TicketSerializer(ticket, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        ticket = self.get_object()
        ticket.status = Ticket.STATUS_CLOSED
        ticket.resolved_at = timezone.now()
        ticket.save()
        return Response(TicketSerializer(ticket, context={"request": request}).data)


class TicketNoteViewSet(viewsets.ModelViewSet):
    serializer_class = TicketNoteSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if self.request.user.is_staff:
            return TicketNote.objects.select_related("ticket")
        return TicketNote.objects.filter(
            ticket__created_by=self.request.user
        ).select_related("ticket")


# ── Page views ──────────────────────────────────────────────────────────────

PAGE_SIZE_CHOICES = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 25


def ticket_list(request):
    tickets = Ticket.objects.filter(created_by=request.user).select_related("incident")

    q               = request.GET.get("q", "").strip()
    status_filter   = request.GET.get("status", "").strip()
    priority_filter = request.GET.get("priority", "").strip()
    if q:
        tickets = tickets.filter(title__icontains=q)
    if status_filter in dict(Ticket.STATUS_CHOICES):
        tickets = tickets.filter(status=status_filter)
    if priority_filter in dict(Ticket.PRIORITY_CHOICES):
        tickets = tickets.filter(priority=priority_filter)

    try:
        page_size = int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    if page_size not in PAGE_SIZE_CHOICES:
        page_size = DEFAULT_PAGE_SIZE

    page_obj = Paginator(tickets, page_size).get_page(request.GET.get("page"))

    # Open incidents for linking when creating a ticket
    open_incidents = Incident.objects.exclude(status=Incident.STATUS_RESOLVED).order_by("title")

    return render(request, "tickets/list.html", {
        "page_obj":         page_obj,
        "filters":          {"q": q, "status": status_filter, "priority": priority_filter},
        "status_choices":   Ticket.STATUS_CHOICES,
        "priority_choices": Ticket.PRIORITY_CHOICES,
        "open_incidents":   open_incidents,
        "page_size":        page_size,
        "page_size_choices": PAGE_SIZE_CHOICES,
    })


def ticket_detail(request, pk):
    ticket = get_object_or_404(
        Ticket.objects.filter(created_by=request.user)
            .select_related("incident")
            .prefetch_related("notes"),
        pk=pk,
    )
    open_incidents = Incident.objects.exclude(status=Incident.STATUS_RESOLVED).order_by("title")
    return render(request, "tickets/detail.html", {
        "ticket":           ticket,
        "notes":            list(ticket.notes.all()),
        "open_incidents":   open_incidents,
        "status_choices":   Ticket.STATUS_CHOICES,
        "priority_choices": Ticket.PRIORITY_CHOICES,
    })
