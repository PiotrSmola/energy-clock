from django.urls import path

from apps.web import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("planner", views.planner, name="planner"),
    path("planner/result", views.planner_result, name="planner_result"),
]
