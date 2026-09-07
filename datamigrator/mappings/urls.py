from django.urls import path

from . import views

app_name = "mappings"

urlpatterns = [
    path("", views.mapping_list, name="list"),
    path("<int:pk>/raw/", views.mapping_raw, name="raw"),
    path("<int:pk>/canvas/", views.mapping_canvas, name="canvas"),
]
