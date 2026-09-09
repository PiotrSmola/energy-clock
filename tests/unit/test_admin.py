from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client

from apps.market.models import GenerationPoint, IngestRun, PricePoint

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client() -> Client:
    user = User.objects.create_superuser("admin", "admin@example.com", "secret-pass-123")
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def sample_rows() -> None:
    PricePoint.objects.create(
        dtime_utc=datetime(2026, 9, 3, 22, 15, tzinfo=UTC),
        business_date=date(2026, 9, 4),
        price_pln_mwh=Decimal("717.86"),
    )
    GenerationPoint.objects.create(
        dtime_utc=datetime(2026, 9, 3, 22, 15, tzinfo=UTC),
        business_date=date(2026, 9, 4),
        pv_mw=Decimal("0"),
        wind_mw=Decimal("5474"),
        conventional_mw=Decimal("9749"),
        carbon_g_kwh=Decimal("480.2"),
    )
    IngestRun.objects.create(
        endpoint=IngestRun.Endpoint.PRICES,
        business_date=date(2026, 9, 4),
        status=IngestRun.Status.OK,
        records=96,
        expected_records=96,
        finished_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "path",
    [
        "/admin/market/ingestrun/",
        "/admin/market/ingestrun/?status=OK&endpoint=csdac-pln",
        "/admin/market/pricepoint/",
        "/admin/market/generationpoint/",
    ],
)
def test_admin_changelists_render(admin_client: Client, sample_rows: None, path: str) -> None:
    response = admin_client.get(path)
    assert response.status_code == 200


def test_ingest_runs_are_read_only(admin_client: Client, sample_rows: None) -> None:
    run = IngestRun.objects.get()
    response = admin_client.get(f"/admin/market/ingestrun/{run.pk}/change/")
    assert response.status_code == 200
    assert b'name="status"' not in response.content
    assert admin_client.get("/admin/market/ingestrun/add/").status_code == 403
