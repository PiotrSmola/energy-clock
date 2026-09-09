from decimal import Decimal

import pytest

from apps.market.carbon import EMISSION_FACTORS_G_KWH, carbon_intensity


def test_emission_factors_match_documented_assumptions() -> None:
    assert EMISSION_FACTORS_G_KWH == {"pv": 0, "wind": 0, "conventional": 750}


def test_pure_renewable_mix_has_zero_intensity() -> None:
    assert carbon_intensity(Decimal("1000"), Decimal("2000"), Decimal("0")) == Decimal("0.0")


def test_pure_conventional_mix_equals_the_factor() -> None:
    assert carbon_intensity(Decimal("0"), Decimal("0"), Decimal("5000")) == Decimal("750.0")


def test_intensity_is_weighted_by_power() -> None:
    assert carbon_intensity(Decimal("1000"), Decimal("1000"), Decimal("2000")) == Decimal("375.0")


@pytest.mark.parametrize(
    ("pv", "wind", "conventional", "expected"),
    [
        ("1000", "0", "1234", "414.3"),
        ("6752", "7831", "9724", "300.0"),
        ("1", "0", "2", "500.0"),
    ],
)
def test_intensity_is_rounded_to_one_decimal(
    pv: str, wind: str, conventional: str, expected: str
) -> None:
    result = carbon_intensity(Decimal(pv), Decimal(wind), Decimal(conventional))
    assert result == Decimal(expected)


def test_all_components_missing_yields_none() -> None:
    assert carbon_intensity(None, None, None) is None


def test_zero_total_generation_yields_none() -> None:
    assert carbon_intensity(Decimal("0"), Decimal("0"), Decimal("0")) is None


def test_negative_total_generation_yields_none() -> None:
    assert carbon_intensity(Decimal("0"), Decimal("0"), Decimal("-5")) is None


def test_missing_components_count_as_zero() -> None:
    assert carbon_intensity(None, None, Decimal("1000")) == Decimal("750.0")
    assert carbon_intensity(Decimal("1000"), None, None) == Decimal("0.0")


def test_result_is_a_decimal() -> None:
    result = carbon_intensity(Decimal("100"), Decimal("100"), Decimal("100"))
    assert isinstance(result, Decimal)
    assert result == Decimal("250.0")
