from django.http import HttpRequest

THEME_COOKIE = "energy_clock_theme"
THEMES = ("light", "dark")
THEME_COOKIE_AGE = 365 * 24 * 60 * 60


def current_theme(request: HttpRequest) -> str:
    requested = request.GET.get("theme", "")
    if requested in THEMES:
        return requested
    value = request.COOKIES.get(THEME_COOKIE, "")
    return value if value in THEMES else "auto"


def theme(request: HttpRequest) -> dict[str, str]:
    return {"theme": current_theme(request)}
