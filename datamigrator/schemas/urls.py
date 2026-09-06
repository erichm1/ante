from django.urls import path

from . import views

app_name = "schemas"

urlpatterns = [
    path("connections/<int:connection_pk>/entities/", views.entity_list, name="entity_list"),
]
