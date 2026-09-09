from decimal import ROUND_HALF_UP, Decimal

EMISSION_FACTORS_G_KWH: dict[str, int] = {"pv": 0, "wind": 0, "conventional": 750}

ONE_DECIMAL = Decimal("0.1")


def carbon_intensity(
    pv_mw: Decimal | None,
    wind_mw: Decimal | None,
    conventional_mw: Decimal | None,
) -> Decimal | None:
    components = {
        "pv": pv_mw,
        "wind": wind_mw,
        "conventional": conventional_mw,
    }
    if all(value is None for value in components.values()):
        return None
    total_mw = sum((value for value in components.values() if value is not None), Decimal(0))
    if total_mw <= 0:
        return None
    weighted_grams = sum(
        (
            value * EMISSION_FACTORS_G_KWH[source]
            for source, value in components.items()
            if value is not None
        ),
        Decimal(0),
    )
    return (weighted_grams / total_mw).quantize(ONE_DECIMAL, rounding=ROUND_HALF_UP)
