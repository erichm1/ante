from django.urls import path

from . import views

app_name = "studio"

urlpatterns = [
    path("", views.studio, name="index"),
    path("tree/", views.tree, name="tree"),
    path("templates/", views.template_catalog, name="templates"),
    path("templates/<slug:slug>/", views.template_apply, name="template_apply"),
]
