from django.urls import path

from apps.web import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("planner", views.planner, name="planner"),
    path("planner/result", views.planner_result, name="planner_result"),
    path("mix", views.mix, name="mix"),
    path("compare", views.compare, name="compare"),
    path("compare/result", views.compare_result, name="compare_result"),
    path("tariff", views.tariff, name="tariff"),
    path("tariff/result", views.tariff_result, name="tariff_result"),
    path("stats", views.stats, name="stats"),
]
