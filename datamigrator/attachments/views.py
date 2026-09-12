from rest_framework import viewsets
from rest_framework.parsers import MultiPartParser, FormParser

from .models import Attachment
from .serializers import AttachmentSerializer


class AttachmentViewSet(viewsets.ModelViewSet):
    serializer_class = AttachmentSerializer
    parser_classes   = [MultiPartParser, FormParser]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        qs = Attachment.objects.select_related("uploaded_by")
        incident = self.request.query_params.get("incident")
        ticket   = self.request.query_params.get("ticket")
        if incident:
            qs = qs.filter(incident_id=incident)
        elif ticket:
            if self.request.user.is_staff:
                qs = qs.filter(ticket_id=ticket)
            else:
                qs = qs.filter(ticket_id=ticket, ticket__created_by=self.request.user)
        elif not self.request.user.is_staff:
            qs = qs.filter(ticket__created_by=self.request.user)
        return qs
