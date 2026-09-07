from django.urls import path

from . import views

app_name = "chains"

urlpatterns = [
    path("", views.chain_list, name="list"),
    path("<int:pk>/", views.chain_detail, name="detail"),
]
