import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal

from apps.market.models import GenerationPoint, PricePoint
from apps.market.queries import price_bucket, res_share_percent
from apps.market.timeutils import INTERVAL, day_bounds_utc, to_local
from apps.planner.optimizer import Window

CHART_WIDTH = 960
CHART_HEIGHT = 260
COMPACT_HEIGHT = 160
MARGIN_LEFT = 52
MARGIN_RIGHT = 12
MARGIN_TOP = 18
MARGIN_BOTTOM = 26
BAR_GAP_RATIO = 0.18
NICE_STEPS = (Decimal(50), Decimal(100), Decimal(200), Decimal(250), Decimal(500), Decimal(1000))
TARGET_TICKS = 4
RING_SIZE = 120
RING_RADIUS = 46
RING_GAP_DEGREES = 2.4


@dataclass(frozen=True, slots=True)
class Bar:
    index: int
    x: float
    y: float
    width: float
    height: float
    start_local: datetime
    end_local: datetime
    price: Decimal
    bucket: int
    res_share: int | None
    negative: bool
    in_window: bool
    is_now: bool


@dataclass(frozen=True, slots=True)
class Tick:
    position: float
    label: str


@dataclass(frozen=True, slots=True)
class ChartModel:
    width: int
    height: int
    left: float
    right: float
    top: float
    bottom: float
    bars: list[Bar]
    y_ticks: list[Tick]
    x_ticks: list[Tick]
    zero_y: float
    now_x: float | None
    window_x1: float | None
    window_x2: float | None
    y_min: Decimal
    y_max: Decimal
    span_start: datetime
    span_end: datetime
    secondary_x1: float | None = None
    secondary_x2: float | None = None

    @property
    def secondary_width(self) -> float:
        if self.secondary_x1 is None or self.secondary_x2 is None:
            return 0.0
        return round(self.secondary_x2 - self.secondary_x1, 2)

    @property
    def span_start_epoch(self) -> int:
        return int(self.span_start.timestamp())

    @property
    def span_end_epoch(self) -> int:
        return int(self.span_end.timestamp())

    @property
    def plot_height(self) -> float:
        return round(self.bottom - self.top, 2)

    @property
    def window_width(self) -> float:
        if self.window_x1 is None or self.window_x2 is None:
            return 0.0
        return round(self.window_x2 - self.window_x1, 2)

    @property
    def label_x(self) -> float:
        return self.left - 8

    @property
    def x_label_y(self) -> float:
        return self.bottom + 18

    @property
    def now_label_y(self) -> float:
        return self.top - 6


def nice_step(span: Decimal) -> Decimal:
    raw = span / TARGET_TICKS
    for step in NICE_STEPS:
        if step >= raw:
            return step
    return NICE_STEPS[-1] * (raw / NICE_STEPS[-1]).quantize(Decimal(1), rounding=ROUND_CEILING)


def axis_bounds(prices: Sequence[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    highest = max(prices)
    lowest = min(prices)
    y_min = min(Decimal(0), lowest)
    step = nice_step(max(highest, Decimal(1)) - y_min)
    y_max = (max(highest, step) / step).quantize(Decimal(1), rounding=ROUND_CEILING) * step
    if y_min < 0:
        y_min = -((-y_min / step).quantize(Decimal(1), rounding=ROUND_CEILING) * step)
    return y_min, y_max, step


def build_chart(
    points: Sequence[PricePoint],
    generation: dict[datetime, GenerationPoint],
    *,
    thresholds: list[Decimal],
    now: datetime | None,
    window: Window | None = None,
    span: tuple[datetime, datetime] | None = None,
    compact: bool = False,
    secondary_window: Window | None = None,
) -> ChartModel:
    height = COMPACT_HEIGHT if compact else CHART_HEIGHT
    left, right = float(MARGIN_LEFT), float(CHART_WIDTH - MARGIN_RIGHT)
    top, bottom = float(MARGIN_TOP), float(height - MARGIN_BOTTOM)
    span_start, span_end = span or day_bounds_utc(points[0].business_date)
    span_seconds = (span_end - span_start).total_seconds()

    def x_of(moment: datetime) -> float:
        return left + (moment - span_start).total_seconds() / span_seconds * (right - left)

    prices = [point.price_pln_mwh for point in points]
    y_min, y_max, step = axis_bounds(prices)
    y_span = float(y_max - y_min)

    def y_of(value: Decimal) -> float:
        return bottom - (float(value) - float(y_min)) / y_span * (bottom - top)

    zero_y = y_of(Decimal(0))
    slot_width = (right - left) * INTERVAL.total_seconds() / span_seconds
    bar_width = slot_width * (1 - BAR_GAP_RATIO)
    bars: list[Bar] = []
    for index, point in enumerate(points):
        start_utc = point.dtime_utc - INTERVAL
        value_y = y_of(point.price_pln_mwh)
        is_now = now is not None and start_utc <= now < point.dtime_utc
        in_window = (
            window is not None and start_utc < window.end_utc and point.dtime_utc > window.start_utc
        )
        bars.append(
            Bar(
                index=index,
                x=round(x_of(start_utc) + (slot_width - bar_width) / 2, 2),
                y=round(min(value_y, zero_y), 2),
                width=round(bar_width, 2),
                height=round(abs(zero_y - value_y), 2),
                start_local=to_local(start_utc),
                end_local=to_local(point.dtime_utc),
                price=point.price_pln_mwh,
                bucket=price_bucket(point.price_pln_mwh, thresholds),
                res_share=res_share_percent(generation.get(point.dtime_utc)),
                negative=point.price_pln_mwh < 0,
                in_window=in_window,
                is_now=is_now,
            )
        )
    y_ticks = []
    tick_value = y_min
    while tick_value <= y_max:
        y_ticks.append(Tick(round(y_of(tick_value), 2), f"{tick_value:.0f}"))
        tick_value += step
    x_ticks = build_x_ticks(span_start, span_end, x_of, compact)
    now_x = round(x_of(now), 2) if now is not None and span_start <= now <= span_end else None
    window_x1, window_x2 = window_span(window, span_start, span_end, x_of)
    secondary_x1, secondary_x2 = window_span(secondary_window, span_start, span_end, x_of)
    return ChartModel(
        width=CHART_WIDTH,
        height=height,
        left=left,
        right=right,
        top=top,
        bottom=bottom,
        bars=bars,
        y_ticks=y_ticks,
        x_ticks=x_ticks,
        zero_y=round(zero_y, 2),
        now_x=now_x,
        window_x1=window_x1,
        window_x2=window_x2,
        y_min=y_min,
        y_max=y_max,
        span_start=span_start,
        span_end=span_end,
        secondary_x1=secondary_x1,
        secondary_x2=secondary_x2,
    )


def window_span(
    window: Window | None,
    span_start: datetime,
    span_end: datetime,
    x_of: Callable[[datetime], float],
) -> tuple[float | None, float | None]:
    if window is None or window.end_utc <= span_start or window.start_utc >= span_end:
        return None, None
    return (
        round(x_of(max(window.start_utc, span_start)), 2),
        round(x_of(min(window.end_utc, span_end)), 2),
    )


def build_x_ticks(
    span_start: datetime,
    span_end: datetime,
    x_of: Callable[[datetime], float],
    compact: bool,
) -> list[Tick]:
    hours_step = 6 if compact else 3
    ticks: list[Tick] = []
    cursor = span_start
    while cursor <= span_end:
        local = to_local(cursor)
        if local.minute == 0 and local.hour % hours_step == 0:
            ticks.append(Tick(round(x_of(cursor), 2), f"{local:%H}"))
        cursor += INTERVAL
    return ticks


MIX_HEIGHT = 300
MIX_RIGHT_MARGIN = 56
MW_STEPS = (
    Decimal(500),
    Decimal(1000),
    Decimal(2000),
    Decimal(2500),
    Decimal(5000),
    Decimal(10000),
)
CARBON_STEPS = (Decimal(50), Decimal(100), Decimal(200), Decimal(250), Decimal(500))
MIX_LAYERS = ("conventional", "wind", "pv")


def nice_ceiling(value: Decimal, steps: tuple[Decimal, ...]) -> tuple[Decimal, Decimal]:
    raw = max(value, Decimal(1)) / TARGET_TICKS
    step = next((candidate for candidate in steps if candidate >= raw), steps[-1])
    ceiling = (max(value, step) / step).quantize(Decimal(1), rounding=ROUND_CEILING) * step
    return ceiling, step


def polyline(points: Sequence[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


@dataclass(frozen=True, slots=True)
class MixLayer:
    key: str
    points: str


@dataclass(frozen=True, slots=True)
class MixChart:
    width: int
    height: int
    left: float
    right: float
    top: float
    bottom: float
    layers: list[MixLayer]
    carbon_segments: list[str]
    y_ticks: list[Tick]
    carbon_ticks: list[Tick]
    x_ticks: list[Tick]
    now_x: float | None
    span_start: datetime
    span_end: datetime
    y_max: Decimal
    carbon_max: Decimal

    @property
    def plot_height(self) -> float:
        return round(self.bottom - self.top, 2)

    @property
    def label_x(self) -> float:
        return self.left - 8

    @property
    def right_label_x(self) -> float:
        return self.right + 8

    @property
    def x_label_y(self) -> float:
        return self.bottom + 18

    @property
    def span_start_epoch(self) -> int:
        return int(self.span_start.timestamp())

    @property
    def span_end_epoch(self) -> int:
        return int(self.span_end.timestamp())


def build_mix_chart(
    points: Sequence[GenerationPoint],
    *,
    now: datetime | None,
    span: tuple[datetime, datetime] | None = None,
) -> MixChart:
    left, right = float(MARGIN_LEFT), float(CHART_WIDTH - MIX_RIGHT_MARGIN)
    top, bottom = float(MARGIN_TOP), float(MIX_HEIGHT - MARGIN_BOTTOM)
    span_start, span_end = span or day_bounds_utc(points[0].business_date)
    span_seconds = (span_end - span_start).total_seconds()

    def x_of(moment: datetime) -> float:
        return left + (moment - span_start).total_seconds() / span_seconds * (right - left)

    values = [
        (
            point.conventional_mw or Decimal(0),
            point.wind_mw or Decimal(0),
            point.pv_mw or Decimal(0),
        )
        for point in points
    ]
    y_max, step = nice_ceiling(max(sum(triple, Decimal(0)) for triple in values), MW_STEPS)
    carbon_values = [point.carbon_g_kwh for point in points if point.carbon_g_kwh is not None]
    carbon_max, carbon_step = nice_ceiling(max(carbon_values, default=Decimal(1)), CARBON_STEPS)

    def y_of(value: Decimal) -> float:
        return bottom - float(value) / float(y_max) * (bottom - top)

    def carbon_y(value: Decimal) -> float:
        return bottom - float(value) / float(carbon_max) * (bottom - top)

    xs = [x_of(point.dtime_utc - INTERVAL) for point in points]
    xs.append(x_of(points[-1].dtime_utc))
    cumulative: list[list[Decimal]] = [[Decimal(0)] * len(points)]
    for layer_index in range(len(MIX_LAYERS)):
        previous = cumulative[-1]
        cumulative.append([previous[i] + values[i][layer_index] for i in range(len(points))])
    layers: list[MixLayer] = []
    for layer_index, key in enumerate(MIX_LAYERS):
        lower = cumulative[layer_index]
        upper = cumulative[layer_index + 1]
        upper_points = [(xs[i], y_of(upper[i])) for i in range(len(points))]
        upper_points.append((xs[-1], y_of(upper[-1])))
        lower_points = [(xs[i], y_of(lower[i])) for i in range(len(points))]
        lower_points.append((xs[-1], y_of(lower[-1])))
        layers.append(MixLayer(key=key, points=polyline(upper_points + lower_points[::-1])))
    carbon_segments: list[str] = []
    current: list[tuple[float, float]] = []
    for index, point in enumerate(points):
        if point.carbon_g_kwh is None:
            if current:
                carbon_segments.append(polyline(current))
                current = []
            continue
        midpoint = (xs[index] + xs[index + 1]) / 2
        current.append((midpoint, carbon_y(point.carbon_g_kwh)))
    if current:
        carbon_segments.append(polyline(current))
    y_ticks = []
    tick = Decimal(0)
    while tick <= y_max:
        y_ticks.append(Tick(round(y_of(tick), 2), f"{tick:.0f}"))
        tick += step
    carbon_ticks = []
    tick = Decimal(0)
    while tick <= carbon_max:
        carbon_ticks.append(Tick(round(carbon_y(tick), 2), f"{tick:.0f}"))
        tick += carbon_step
    now_x = round(x_of(now), 2) if now is not None and span_start <= now <= span_end else None
    return MixChart(
        width=CHART_WIDTH,
        height=MIX_HEIGHT,
        left=left,
        right=right,
        top=top,
        bottom=bottom,
        layers=layers,
        carbon_segments=carbon_segments,
        y_ticks=y_ticks,
        carbon_ticks=carbon_ticks,
        x_ticks=build_x_ticks(span_start, span_end, x_of, False),
        now_x=now_x,
        span_start=span_start,
        span_end=span_end,
        y_max=y_max,
        carbon_max=carbon_max,
    )


BARS_HEIGHT = 200
BARS_STEPS = (
    Decimal(1),
    Decimal(2),
    Decimal(5),
    Decimal(10),
    Decimal(20),
    Decimal(25),
    Decimal(50),
    Decimal(100),
    Decimal(200),
    Decimal(250),
    Decimal(500),
    Decimal(1000),
    Decimal(2000),
    Decimal(5000),
)


@dataclass(frozen=True, slots=True)
class SimpleBar:
    x: float
    y: float
    width: float
    height: float
    label: str
    value_text: str
    css_class: str
    negative: bool

    @property
    def center_x(self) -> float:
        return round(self.x + self.width / 2, 2)

    @property
    def value_y(self) -> float:
        if self.negative:
            return round(self.y + self.height + 12, 2)
        return round(self.y - 5, 2)


@dataclass(frozen=True, slots=True)
class BarsChart:
    width: int
    height: int
    left: float
    right: float
    top: float
    bottom: float
    bars: list[SimpleBar]
    y_ticks: list[Tick]
    zero_y: float

    @property
    def label_x(self) -> float:
        return self.left - 8

    @property
    def x_label_y(self) -> float:
        return self.bottom + 18


def build_bars(
    items: Sequence[tuple[str, Decimal, str, str]],
    *,
    height: int = BARS_HEIGHT,
    y_max: Decimal | None = None,
) -> BarsChart:
    left, right = float(MARGIN_LEFT), float(CHART_WIDTH - MARGIN_RIGHT)
    top, bottom = float(MARGIN_TOP), float(height - MARGIN_BOTTOM)
    values = [value for _label, value, _text, _css in items] or [Decimal(0)]
    highest = max(max(values), Decimal(0))
    lowest = min(min(values), Decimal(0))
    span_value = max(highest - lowest, Decimal(1))
    ceiling, step = nice_ceiling(highest if y_max is None else y_max, BARS_STEPS)
    if y_max is not None:
        ceiling = y_max
        _unused, step = nice_ceiling(y_max, BARS_STEPS)
    floor = Decimal(0)
    if lowest < 0:
        floor = -((-lowest / step).quantize(Decimal(1), rounding=ROUND_CEILING) * step)
    span_value = max(ceiling - floor, Decimal(1))

    def y_of(value: Decimal) -> float:
        return bottom - float(value - floor) / float(span_value) * (bottom - top)

    zero_y = y_of(Decimal(0))
    slot = (right - left) / max(len(items), 1)
    bar_width = slot * 0.72
    bars = [
        SimpleBar(
            x=round(left + index * slot + (slot - bar_width) / 2, 2),
            y=round(min(y_of(value), zero_y), 2),
            width=round(bar_width, 2),
            height=round(abs(zero_y - y_of(value)), 2),
            label=label,
            value_text=text,
            css_class=css,
            negative=value < 0,
        )
        for index, (label, value, text, css) in enumerate(items)
    ]
    y_ticks = []
    tick = floor
    while tick <= ceiling:
        y_ticks.append(Tick(round(y_of(tick), 2), f"{tick:.0f}"))
        tick += step
    return BarsChart(
        width=CHART_WIDTH,
        height=height,
        left=left,
        right=right,
        top=top,
        bottom=bottom,
        bars=bars,
        y_ticks=y_ticks,
        zero_y=round(zero_y, 2),
    )


@dataclass(frozen=True, slots=True)
class RingSegment:
    path: str
    bucket: int
    label: str


@dataclass(frozen=True, slots=True)
class RingModel:
    size: int
    segments: list[RingSegment]
    hand_angle: float | None
    hand_x: float
    hand_y: float


def polar(angle_degrees: float, radius: float) -> tuple[float, float]:
    radians = math.radians(angle_degrees)
    center = RING_SIZE / 2
    return center + radius * math.sin(radians), center - radius * math.cos(radians)


def build_ring(
    points: Sequence[PricePoint], thresholds: list[Decimal], now: datetime | None
) -> RingModel:
    hourly: dict[int, list[Decimal]] = {}
    for point in points:
        hour = to_local(point.dtime_utc - INTERVAL).hour
        hourly.setdefault(hour, []).append(point.price_pln_mwh)
    segments: list[RingSegment] = []
    for hour in range(24):
        prices = hourly.get(hour)
        if not prices:
            continue
        average = sum(prices, Decimal(0)) / len(prices)
        start_angle = hour * 15 + RING_GAP_DEGREES / 2
        end_angle = (hour + 1) * 15 - RING_GAP_DEGREES / 2
        x1, y1 = polar(start_angle, RING_RADIUS)
        x2, y2 = polar(end_angle, RING_RADIUS)
        segments.append(
            RingSegment(
                path=f"M{x1:.2f} {y1:.2f}A{RING_RADIUS} {RING_RADIUS} 0 0 1 {x2:.2f} {y2:.2f}",
                bucket=price_bucket(average, thresholds),
                label=f"{hour:02d}:00 {average:.0f}",
            )
        )
    hand_angle = None
    hand_x = hand_y = RING_SIZE / 2
    if now is not None:
        local_now = to_local(now)
        hand_angle = (local_now.hour + local_now.minute / 60) * 15
        hand_x, hand_y = polar(hand_angle, RING_RADIUS + 9)
    return RingModel(
        size=RING_SIZE,
        segments=segments,
        hand_angle=hand_angle,
        hand_x=round(hand_x, 2),
        hand_y=round(hand_y, 2),
    )
