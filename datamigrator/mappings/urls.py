from django.urls import path

from . import views

app_name = "mappings"

urlpatterns = [
    path("", views.mapping_list, name="list"),
    path("canvas/", views.canvas_list, name="canvas_list"),
    path("<int:pk>/", views.mapping_detail, name="detail"),
]
