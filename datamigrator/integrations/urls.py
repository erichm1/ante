from django.urls import path

from . import views

app_name = "integrations"

urlpatterns = [
    path("", views.app_store_list, name="list"),
    path("<int:pk>/install/", views.integration_install, name="install"),
    path("installs/<int:pk>/uninstall/", views.integration_uninstall, name="uninstall"),
    path("connections/<int:pk>/reconnect/", views.reconnect_connection, name="reconnect"),
    path("oauth/callback/", views.oauth_callback, name="oauth_callback"),
]
