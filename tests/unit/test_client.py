from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx
import pytest
import respx

from apps.market.client import USER_AGENT, PseApiError, PseClient

BASE_URL = "https://api.raporty.pse.pl/api"
PRICES_URL = f"{BASE_URL}/csdac-pln"


def price_record(dtime_utc: str, price: float) -> dict[str, Any]:
    return {
        "business_date": "2026-09-04",
        "dtime_utc": dtime_utc,
        "csdac_pln": price,
        "publication_ts_utc": "2026-09-03 11:53:17.304",
    }


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture
def sleep() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
def client(sleep: SleepRecorder) -> Iterator[PseClient]:
    with PseClient(BASE_URL, retries=3, backoff_seconds=0.5, sleep=sleep) as pse:
        yield pse


@respx.mock
def test_fetch_day_encodes_odata_filter_and_sets_user_agent(client: PseClient) -> None:
    route = respx.get(PRICES_URL).mock(
        return_value=httpx.Response(200, json={"value": [price_record("2026-09-03 22:15:00", 1)]})
    )

    records = client.fetch_day("csdac-pln", date(2026, 9, 4))

    assert len(records) == 1
    request = route.calls.last.request
    assert request.headers["User-Agent"] == USER_AGENT
    assert "%24filter=business_date%20eq%20%272026-09-04%27" in str(request.url)
    assert "%24first=1000" in str(request.url)


@respx.mock
def test_fetch_follows_next_link_until_exhausted(client: PseClient) -> None:
    next_link = f"{PRICES_URL}?%24first=2&%24after=TOKEN"
    respx.get(PRICES_URL, params__contains={"$first": "2"}).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "value": [
                        price_record("2026-09-03 22:15:00", 1),
                        price_record("2026-09-03 22:30:00", 2),
                    ],
                    "nextLink": next_link,
                },
            ),
            httpx.Response(200, json={"value": [price_record("2026-09-03 22:45:00", 3)]}),
        ]
    )

    records = list(client.fetch("csdac-pln", page_size=2))

    assert [record["csdac_pln"] for record in records] == [1, 2, 3]
    second_request = respx.calls[1].request
    assert str(second_request.url) == next_link


@respx.mock
def test_empty_value_list_is_a_valid_response(client: PseClient) -> None:
    respx.get(PRICES_URL).mock(return_value=httpx.Response(200, json={"value": []}))

    assert client.fetch_day("csdac-pln", date(2026, 9, 5)) == []


@respx.mock
def test_retries_on_server_errors_with_backoff(client: PseClient, sleep: SleepRecorder) -> None:
    route = respx.get(PRICES_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(500),
            httpx.Response(200, json={"value": [price_record("2026-09-03 22:15:00", 1)]}),
        ]
    )

    records = client.fetch_day("csdac-pln", date(2026, 9, 4))

    assert len(records) == 1
    assert route.call_count == 3
    assert sleep.delays == [0.5, 1.0]


@respx.mock
def test_gives_up_after_configured_retries(client: PseClient, sleep: SleepRecorder) -> None:
    route = respx.get(PRICES_URL).mock(return_value=httpx.Response(502))

    with pytest.raises(PseApiError, match="502"):
        client.fetch_day("csdac-pln", date(2026, 9, 4))

    assert route.call_count == 4
    assert sleep.delays == [0.5, 1.0, 2.0]


@respx.mock
def test_retries_on_transport_errors(client: PseClient, sleep: SleepRecorder) -> None:
    route = respx.get(PRICES_URL).mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.ReadTimeout("slow"),
            httpx.Response(200, json={"value": []}),
        ]
    )

    assert client.fetch_day("csdac-pln", date(2026, 9, 4)) == []
    assert route.call_count == 3
    assert sleep.delays == [0.5, 1.0]


@respx.mock
def test_client_errors_are_not_retried(client: PseClient, sleep: SleepRecorder) -> None:
    route = respx.get(f"{BASE_URL}/no-such-endpoint").mock(return_value=httpx.Response(404))

    with pytest.raises(PseApiError, match="404"):
        list(client.fetch("no-such-endpoint"))

    assert route.call_count == 1
    assert sleep.delays == []


@respx.mock
def test_fetch_range_uses_inclusive_date_filter(client: PseClient) -> None:
    route = respx.get(PRICES_URL).mock(return_value=httpx.Response(200, json={"value": []}))

    list(client.fetch_range("csdac-pln", date(2026, 8, 28), date(2026, 9, 3)))

    assert route.calls.last.request.url.params["$filter"] == (
        "business_date ge '2026-08-28' and business_date le '2026-09-03'"
    )


def test_default_timeout_is_thirty_seconds() -> None:
    with PseClient(BASE_URL) as pse:
        assert pse.timeout_seconds == 30.0
