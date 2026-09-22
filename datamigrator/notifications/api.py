"""REST API for a person's own notifications.

    GET    /api/notifications/                     list  (?unread=1, ?outcome=failed, ?kind=plan)
    GET    /api/notifications/<id>/                one
    DELETE /api/notifications/<id>/                dismiss it
    POST   /api/notifications/<id>/read/           mark it read
    POST   /api/notifications/<id>/unread/         mark it unread again
    POST   /api/notifications/mark-all-read/       mark every unread one read  → {"updated": n, "unread": 0}
    GET    /api/notifications/unread-count/        → {"unread": n}

Everything is scoped to the signed-in user: nobody can see or change anyone else's notifications.
"""
from rest_framework import mixins, permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Notification
from .services import mark_all_read


class NotificationSerializer(serializers.ModelSerializer):
    level = serializers.CharField(read_only=True)
    read = serializers.SerializerMethodField()
    open_url = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ["id", "kind", "outcome", "level", "title", "message", "url", "open_url", "source_id", "read", "created_at", "read_at"]
        read_only_fields = fields

    def get_read(self, obj):
        return obj.read_at is not None

    def get_open_url(self, obj):
        """Marks it read and redirects to what it is about."""
        return f"/notifications/{obj.pk}/go/"


class NotificationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = []

    def get_queryset(self):
        qs = Notification.objects.for_user(self.request.user)
        params = self.request.query_params
        if params.get("unread") in ("1", "true", "True"):
            qs = qs.unread()
        if params.get("outcome") in dict(Notification.OUTCOME_CHOICES):
            qs = qs.filter(outcome=params["outcome"])
        if params.get("kind") in dict(Notification.KIND_CHOICES):
            qs = qs.filter(kind=params["kind"])
        return qs

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        return Response({"updated": mark_all_read(request.user), "unread": 0})

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"unread": Notification.objects.for_user(request.user).unread().count()})

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        n = self.get_object()
        Notification.objects.filter(pk=n.pk).mark_all_read()
        n.refresh_from_db()
        return Response(self.get_serializer(n).data)

    @action(detail=True, methods=["post"])
    def unread(self, request, pk=None):
        n = self.get_object()
        Notification.objects.filter(pk=n.pk).update(read_at=None)
        n.refresh_from_db()
        return Response(self.get_serializer(n).data)
