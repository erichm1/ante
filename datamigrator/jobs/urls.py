from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("", views.run_list, name="run_list"),
    path("runs/<int:pk>/", views.run_detail, name="run_detail"),
    path("runs/<int:pk>/snapshot/", views.run_snapshot, name="run_snapshot"),
]
