from django.urls import path

from . import views

app_name = "connections"

urlpatterns = [
    path("", views.connection_list, name="list"),
    path("logs/", views.api_log_list, name="logs"),
    path("<int:pk>/", views.connection_detail, name="detail"),
    path("<int:pk>/secrets/basic/", views.save_basic_secrets, name="save_basic_secrets"),
    path("<int:pk>/secrets/bearer/", views.save_bearer_token, name="save_bearer_token"),
    path("<int:pk>/secrets/jwt/", views.save_jwt_config, name="save_jwt_config"),
    path("<int:pk>/oauth/authorize/", views.oauth_authorize, name="oauth_authorize"),
    path("<int:pk>/oauth/callback/", views.oauth_callback, name="oauth_callback"),
]
