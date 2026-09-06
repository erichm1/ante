from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("runs/<int:pk>/", views.run_detail, name="run_detail"),
]
