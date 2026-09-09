from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils import translation


class CookieLocaleMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self.supported = {code for code, _name in settings.LANGUAGES}

    def __call__(self, request: HttpRequest) -> HttpResponse:
        language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, "")
        if language not in self.supported:
            language = settings.LANGUAGE_CODE
        translation.activate(language)
        request.LANGUAGE_CODE = language
        try:
            response = self.get_response(request)
        finally:
            translation.deactivate()
        response.headers.setdefault("Content-Language", language)
        patch_vary(response)
        return response


def patch_vary(response: HttpResponse) -> None:
    existing = response.headers.get("Vary", "")
    parts = [part.strip() for part in existing.split(",") if part.strip()]
    if "Cookie" not in parts:
        parts.append("Cookie")
    response.headers["Vary"] = ", ".join(parts)
