# Energy Clock

**When should you switch on the washing machine to pay less for electricity?**

Energy Clock reads the Polish day-ahead electricity market (PSE, 15-minute intervals),
shows today's and tomorrow's prices as a clock, and answers one question for a concrete
appliance: *start it at 14:45 instead of now and you will pay 0.71 PLN less*. It also shows
when the grid is cleanest, because the cheapest hour is not always the greenest one.

![Energy Clock widget](http://127.0.0.1:8000/widget.svg)

- Live price clock with the cheapest and most expensive windows of the day
- Appliance planner: cheapest start for a given cycle length and power draw, with constraints
  such as "finish by 07:00"
- Cheap vs. clean: two rankings side by side and the price of choosing the cleaner window
- Generation mix and carbon intensity from the PSE generation plan
- Tariff calculator: would a dynamic tariff have paid off month by month?
- Market statistics computed by the database (hour of day, weekday, renewable share,
  negative prices)
- Accounts with a household profile, own appliances and email alerts
- Embeddable widget, SVG badge, CSV export and a small JSON API
- English by default, Polish via the header switch; light and dark theme

## Who it is for

Households on a dynamic (spot-indexed) tariff, owners of heat pumps, EVs and PV
installations, and anyone curious how much the price of electricity moves during the day.
It is a portfolio project: the goal is a small, complete, well-tested Django application
with a real data pipeline, not a commercial product.

## Architecture

```
PSE reports API ──httpx──▶ apps.market (ingest, models, stats)
                              │  SQLite (WAL), UTC in the database
Celery beat ─▶ worker ────────┤  Redis: broker + Django cache
                              ▼
                       apps.planner (optimizer, tariff, alerts)
                              ▼
                       apps.web (views, SVG charts, i18n, API, widget)
                              ▼
               Django templates + HTMX + Alpine.js + one CSS file
```

| Layer | What it does |
|---|---|
| `apps/market` | PSE client with pagination and retries, idempotent ingest keyed by `dtime_utc`, DST-aware interval counting (92 / 96 / 100 per day), carbon intensity estimate, Celery tasks and beat schedule, backfill command, database-side statistics |
| `apps/planner` | Pure optimizer (`cheapest_window`, `greenest_window`, `cost_if_started_now`, non-overlapping rankings), appliance templates, tariff simulator with exact daily profiles (`Fraction` weights), household, alerts with once-per-day delivery |
| `apps/web` | Server-rendered pages and HTMX partials, SVG charts built in Python, cookie-based language and theme, accounts, JSON API, CSV export, widget |

Schedule (Celery beat, stored in the database via django-celery-beat):

| When (Europe/Warsaw) | Task |
|---|---|
| 14:15, 15:15, 16:15 | fetch tomorrow's prices, retry with backoff while PSE has not published yet, then check alerts |
| every hour at :05 | fetch today's and tomorrow's generation plan |
| 03:30 | fill gaps from the last 7 days |
| 04:00, 15:00 | warm the statistics cache |
| worker start | fill gaps, so a machine that was off catches up by itself |

## Design decisions

**SQLite instead of PostgreSQL.** Two years of 15-minute prices and generation plans are
about 160 000 rows; the whole database is a few tens of megabytes and every query here
runs in milliseconds. WAL mode lets the web server and the Celery worker write
concurrently. Running a database server for this volume would be ceremony. Switching to
PostgreSQL is a one-dictionary change in `config/settings.py`; the only SQLite-specific
code is the pragma receiver in `apps/market/sqlite.py`.

**HTMX and Alpine.js instead of a single-page application.** The interactions are few and
well defined: switch a day, recompute a plan, change a form. Django renders complete
pages, HTMX swaps fragments, Alpine.js handles local reactivity (sliders with an instant
cost preview). There is no build step, every page works with JavaScript disabled, and the
charts are SVG generated on the server, so the same code renders the README badge.

**Celery beat instead of cron.** PSE publishes tomorrow's prices around 14:00, sometimes
later. The fetch task retries with exponential backoff, every attempt is recorded as an
`IngestRun` visible in the admin, and the schedule lives in the database where it can be
inspected and changed. A cron line cannot express "retry until published".

**`dtime_utc` as the natural key.** PSE marks each interval with the UTC timestamp of its
*end*. On the day the clocks change a `(business_date, period)` pair is ambiguous or has
holes; UTC is monotonic. The expected number of intervals per day is computed from the
UTC offsets at the start and end of the local day, never hard-coded.

**Hourly aggregation on phones.** Ninety-six bars do not fit in 380 px. Instead of a
horizontally scrolling chart that hides the "now" marker, narrow screens get a second SVG
with one bar per hour (average price, same colour scale); the full 15-minute table stays
one tap away. Both charts are rendered server-side and switched with a media query.

**Theme in a cookie.** The server renders `data-theme` from the cookie, so the dark palette
is applied before the first paint and there is no flash. Without a cookie the page follows
`prefers-color-scheme`. The dark theme is a separate palette, not an inversion.

**Redis as the Django cache.** Statistics over 80 000 intervals take a few seconds to
aggregate; they are cached for six hours in Redis (shared by the web server and the
worker) and warmed by a Celery task after each publication.

**English default, Polish second.** Every string goes through gettext; the language lives
in a cookie and is never guessed from `Accept-Language`, so the default is stable.

## Caveats, shown in the interface as well

- **Wholesale prices, not your bill.** PSE publishes day-ahead market prices. Your supplier
  adds a margin, and distribution fees, excise duty and VAT come on top. The application
  shows *relative* differences within the day; they translate into savings only on a
  dynamic tariff.
- **Savings are estimates.** Costs assume a constant power draw for the whole cycle. A real
  washing machine does not draw 2 kW evenly for two hours.
- **Carbon intensity is approximate.** It is derived from the *planned* generation mix with
  0 g/kWh for solar and wind and 750 g/kWh for conventional units, a rough average for the
  Polish mix; imports, grid losses and the real efficiency of individual plants are ignored.
  Values are always shown with "≈".
- **History starts on 14 June 2024**, when the Polish 15-minute day-ahead market started.
  The tariff calculator cannot look further back.
- **No password reset.** Mail goes only to the local Mailpit inbox, so there is no way to
  deliver a reset link. Accounts are email plus password, nothing else.

## Running it

Requirements: Docker Desktop with the Compose plugin. Nothing is installed on the host.

```powershell
./make.ps1 up        # redis + mailpit
./make.ps1 migrate
./make.ps1 dev       # web on http://127.0.0.1:8000, worker, beat
```

Useful addresses: application `http://127.0.0.1:8000`, Mailpit `http://127.0.0.1:8025`,
admin `http://127.0.0.1:8000/admin/` (create a superuser with `./make.ps1 super`).

Quality gates:

```powershell
./make.ps1 test      # pytest, offline (live PSE tests are opt-in via -m live)
./make.ps1 lint      # ruff
./make.ps1 types     # mypy --strict
```

## Reproducing the data

The database is not in the repository (`data/` is ignored; `data/MANIFEST.md` documents the
source). One command downloads the whole history:

```powershell
./make.ps1 backfill
```

or, inside the container, with a range or a single endpoint:

```bash
python manage.py backfill --start 2026-01-01 --end 2026-03-31
python manage.py backfill --endpoint prices
```

Backfill skips days that are already complete, so it can be re-run at any time. The full
history from 14 June 2024 is roughly 80 000 price intervals and takes a few minutes.

## API and export

| Endpoint | Description |
|---|---|
| `/api/prices?date=2026-09-04` | one day of prices with the generation plan and carbon intensity |
| `/api/windows?duration=120&power=2.0&finish_by=07:00` | cheapest, greenest and "start now" windows for a cycle |
| `/export.csv?start=2026-09-01&end=2026-09-07` | CSV of prices and generation, up to 400 days |
| `/widget` | embeddable page for an `<iframe>` |
| `/widget.svg` | server-rendered badge for a README |

All money values are strings to preserve decimals; timestamps are ISO 8601 in UTC and in
Europe/Warsaw.

## Development notes

- Python 3.13, Django 5.2, Celery 5.6, `httpx`, `Decimal` everywhere money or power is
  involved, `mypy --strict`, `ruff`.
- Tests use fixtures with real PSE data for 4 and 5 September 2026 and never touch the
  network; `time-machine` freezes the clock.
- Translations: `python manage.py makemessages -l pl` and `compilemessages` (gettext is in
  the image); the compiled catalog is committed so a clean clone runs in both languages.
- Font: Bricolage Grotesque, self-hosted under the SIL Open Font License
  (`static/fonts/OFL-BricolageGrotesque.txt`).

## Attribution

Price and generation data come from the public
[PSE reports API](https://api.raporty.pse.pl/) (Polskie Sieci Elektroenergetyczne):
`csdac-pln` for day-ahead prices and `pdgobpkd` for the generation plan. Energy Clock is an
independent project and is not affiliated with PSE.
