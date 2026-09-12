from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="accounts:login"), name="logout"),
    path("register/", views.RegisterView.as_view(), name="register"),
    path("profile/", views.profile, name="profile"),
]
