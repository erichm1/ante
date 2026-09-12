from django.urls import path

from . import views

app_name = "incidents"

urlpatterns = [
    path("", views.incident_list, name="list"),
    path("<int:pk>/", views.incident_detail, name="detail"),
    path("<int:pk>/postmortem/", views.incident_postmortem, name="postmortem"),
]
