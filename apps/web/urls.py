from django.urls import path

from apps.web import accounts, api, views, widget

app_name = "web"

urlpatterns = [
    path("theme", views.set_theme, name="set_theme"),
    path("widget", widget.widget, name="widget"),
    path("widget.svg", widget.widget_svg, name="widget_svg"),
    path("export.csv", api.export_csv, name="export_csv"),
    path("api/prices", api.prices, name="api_prices"),
    path("api/windows", api.windows, name="api_windows"),
    path("account", accounts.profile, name="profile"),
    path("account/register", accounts.register, name="register"),
    path("account/login", accounts.SignInView.as_view(), name="login"),
    path("account/logout", accounts.SignOutView.as_view(), name="logout"),
    path(
        "account/appliances/<int:appliance_id>/delete",
        accounts.delete_appliance,
        name="delete_appliance",
    ),
    path("account/alerts/<int:alert_id>/toggle", accounts.toggle_alert, name="toggle_alert"),
    path("account/alerts/<int:alert_id>/delete", accounts.delete_alert, name="delete_alert"),
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
