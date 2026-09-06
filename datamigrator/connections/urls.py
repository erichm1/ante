from django.urls import path

from . import views

app_name = "connections"

urlpatterns = [
    path("", views.connection_list, name="list"),
    path("<int:pk>/", views.connection_detail, name="detail"),
    path("<int:pk>/secrets/basic/", views.save_basic_secrets, name="save_basic_secrets"),
    path("<int:pk>/secrets/api-key/", views.save_api_key, name="save_api_key"),
    path("<int:pk>/oauth/authorize/", views.oauth_authorize, name="oauth_authorize"),
    path("<int:pk>/oauth/callback/", views.oauth_callback, name="oauth_callback"),
]
