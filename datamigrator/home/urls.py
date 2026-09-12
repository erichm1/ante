from django.urls import path

from . import views

app_name = "home"

urlpatterns = [
    path("", views.index, name="index"),
    path("stats/", views.stats, name="stats"),
    path("status/", views.status_page, name="status"),
    path("status/data/", views.status_data, name="status_data"),
    path("check-rules/", views.check_rules, name="check_rules"),
    path("rules/", views.rules_api, name="rules_api"),
    path("rules/<int:pk>/", views.rule_detail_api, name="rule_detail_api"),
]
