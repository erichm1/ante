from django.urls import path

from . import views

app_name = "plans"

urlpatterns = [
    path("", views.plan_list, name="list"),
    path("<int:pk>/", views.plan_detail, name="detail"),
]
