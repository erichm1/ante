from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list, name="list"),
    path("feed/", views.feed, name="feed"),
    path("read/", views.mark_read, name="mark_read"),
    path("<int:pk>/go/", views.go, name="go"),
]
