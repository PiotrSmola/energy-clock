import logging
import time
from collections.abc import Callable, Iterator
from datetime import date
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote, urlencode

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

USER_AGENT = "energy-clock/1.0"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_PAGE_SIZE = 1000


class PseApiError(Exception):
    pass


class PseClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        resolved_base = base_url if base_url is not None else str(settings.PSE_API_BASE)
        self.timeout_seconds = timeout
        self._retries = retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=resolved_base.rstrip("/") + "/",
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def fetch(
        self,
        endpoint: str,
        *,
        filter_expr: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Iterator[dict[str, Any]]:
        query: dict[str, str] = {"$first": str(page_size)}
        if filter_expr:
            query["$filter"] = filter_expr
        url: str | None = f"{endpoint}?{urlencode(query, quote_via=quote)}"
        while url:
            payload = self._get_json(url)
            yield from payload.get("value", [])
            url = payload.get("nextLink") or None

    def fetch_day(self, endpoint: str, day: date) -> list[dict[str, Any]]:
        return list(self.fetch(endpoint, filter_expr=f"business_date eq '{day.isoformat()}'"))

    def fetch_range(self, endpoint: str, start: date, end: date) -> Iterator[dict[str, Any]]:
        filter_expr = (
            f"business_date ge '{start.isoformat()}' and business_date le '{end.isoformat()}'"
        )
        return self.fetch(endpoint, filter_expr=filter_expr)

    def _get_json(self, url: str) -> dict[str, Any]:
        attempt = 0
        while True:
            attempt += 1
            failure = self._attempt(url)
            if isinstance(failure, dict):
                return failure
            if attempt > self._retries:
                raise PseApiError(f"PSE request failed after {attempt} attempts: {failure}")
            delay = self._backoff_seconds * 2 ** (attempt - 1)
            logger.warning(
                "PSE request failed (%s), retrying in %.1fs (attempt %d/%d)",
                failure,
                delay,
                attempt,
                self._retries,
            )
            self._sleep(delay)

    def _attempt(self, url: str) -> dict[str, Any] | str:
        try:
            response = self._http.get(url)
        except httpx.TransportError as exc:
            return f"{type(exc).__name__}: {exc}"
        if response.status_code >= 500:
            return f"HTTP {response.status_code}"
        if response.status_code >= 400:
            raise PseApiError(f"PSE request rejected with HTTP {response.status_code}: {url}")
        payload: dict[str, Any] = response.json()
        return payload
