#Requires -Version 5.1
[CmdletBinding()]
param([switch]$AllowNonEmpty)

$ErrorActionPreference = 'Stop'

function Write-Step { param([string]$Message) Write-Host "`n> $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "  [ok]   $Message" -ForegroundColor Green }
function Write-Skip { param([string]$Message) Write-Host "  [skip] $Message" -ForegroundColor Yellow }
function Write-Warn { param([string]$Message) Write-Host "  [warn] $Message" -ForegroundColor Yellow }
function Write-Fail { param([string]$Message) Write-Host "`n[FAIL] $Message" -ForegroundColor Red; exit 1 }

function Write-ProjectFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][AllowEmptyCollection()][string[]]$Lines
    )
    if (Test-Path $Path) { Write-Skip "$Path (juz istnieje)"; return }
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $eol = "`n"
    if ($Path -match '\.(ps1|bat|cmd)$') { $eol = "`r`n" }
    $text = ($Lines -join $eol) + $eol
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path (Get-Location).Path $Path), $text, $utf8NoBom)
    Write-Ok $Path
}

$ProjectName = 'energy-clock'

function Get-ComposeBase {
    return @('-p', $ProjectName, '-f', (Join-Path (Get-Location).Path 'compose.yml'))
}

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]]$CommandArgs)
    $base = Get-ComposeBase
    & docker compose @base @CommandArgs
}

function Invoke-Web {
    param([Parameter(Mandatory = $true)][string[]]$CommandArgs, [switch]$Tolerant)
    $base = Get-ComposeBase
    & docker compose @base run --rm --no-deps web @CommandArgs
    if ($LASTEXITCODE -ne 0 -and -not $Tolerant) { throw "polecenie zwrocilo kod $LASTEXITCODE" }
}

Write-Step 'Sprawdzanie wymagan hosta'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Fail 'Brak Dockera. Zainstaluj Docker Desktop.' }
& docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Fail 'Brak "docker compose" (plugin v2).' }
& docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Fail 'Docker daemon nie odpowiada. Uruchom Docker Desktop.' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Fail 'Brak gita.' }

Write-Ok ('docker ' + (& docker version --format '{{.Server.Version}}'))
Write-Ok ('compose ' + (& docker compose version --short))

$hostOs = & docker info --format '{{.OperatingSystem}}' 2>$null
if ($hostOs -match 'WSL|Docker Desktop') { Write-Ok "backend: $hostOs" }
else { Write-Warn 'Nie wykryto WSL2. Settings -> General -> Use WSL 2 based engine' }

Write-Step 'Kontrola katalogu docelowego'

$allowed = @('bootstrap-energy.ps1', 'bootstrap-energy.bat', 'PROMPT_ENERGY_DATA.md', 'PLAN_ENERGY_CLOCK.md', '_local')
$foreign = Get-ChildItem -Force | Where-Object { $allowed -notcontains $_.Name }

if ($foreign.Count -gt 0 -and -not $AllowNonEmpty) {
    Write-Host ''
    Write-Host '  Katalog nie jest pusty. Znalezione elementy:' -ForegroundColor Yellow
    $foreign | Select-Object -First 12 | ForEach-Object { Write-Host ('    ' + $_.Name) }
    if ($foreign.Count -gt 12) { Write-Host ('    ... i ' + ($foreign.Count - 12) + ' wiecej') }
    Write-Host ''
    Write-Host '  Skrypt buduje obraz z lokalnego Dockerfile i uruchamia' -ForegroundColor Yellow
    Write-Host '  scripts/verify.ps1. Jesli te pliki juz istnieja, wykonalby' -ForegroundColor Yellow
    Write-Host '  cudzy kod zamiast wlasnego.' -ForegroundColor Yellow
    Write-Host ''
    Write-Fail 'Uruchom w pustym katalogu albo wymus: .\bootstrap-energy.ps1 -AllowNonEmpty'
}
if ($foreign.Count -gt 0) { Write-Warn ('pominieto kontrole, obcych elementow: ' + $foreign.Count) }
else { Write-Ok 'katalog czysty' }

Write-Step 'Inicjalizacja repozytorium'

if (Test-Path '.git') { Write-Skip '.git juz istnieje' } else { & git init -q; Write-Ok 'git init' }
& git config core.autocrlf false
Write-Ok 'core.autocrlf = false'

Write-Step 'Struktura katalogow'

$dirs = @(
    'config',
    'apps/market/management/commands', 'apps/market/migrations',
    'apps/planner', 'apps/web',
    'templates', 'static/css', 'static/js',
    'data', 'scripts', 'docs',
    'tests/unit', 'tests/integration', 'tests/fixtures',
    '.devcontainer'
)
foreach ($d in $dirs) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
Write-Ok 'katalogi utworzone'

Write-Step 'Pliki konfiguracyjne'

Write-ProjectFile '.gitattributes' @(
    '* text=auto eol=lf',
    '*.ps1 text eol=crlf',
    '*.bat text eol=crlf',
    '*.py text eol=lf'
)

Write-ProjectFile '.gitignore' @(
    '__pycache__/',
    '*.py[cod]',
    '.env',
    'data/',
    '!data/MANIFEST.md',
    '.pytest_cache/',
    '.mypy_cache/',
    '.ruff_cache/',
    'htmlcov/',
    '.coverage',
    'staticfiles/',
    '',
    '_local/',
    'CLAUDE.md',
    'AGENTS.md',
    '.claude/',
    '.cursor/',
    'PROMPT_*.md',
    'PLAN_*.md'
)

Write-ProjectFile '.dockerignore' @(
    '__pycache__',
    '.git',
    '.env',
    'data',
    '.pytest_cache',
    '.mypy_cache',
    '_local'
)

Write-ProjectFile 'requirements.txt' @(
    'Django>=5.1,<6',
    'celery>=5.4',
    'redis>=5.0',
    'django-celery-beat>=2.7',
    'django-celery-results>=2.5',
    'httpx>=0.27',
    'python-dotenv>=1.0',
    'tqdm>=4.66',
    'whitenoise>=6.7'
)

Write-ProjectFile 'requirements-dev.txt' @(
    '-r requirements.txt',
    'pytest>=8.0',
    'pytest-django>=4.8',
    'pytest-cov>=5.0',
    'time-machine>=2.15',
    'respx>=0.21',
    'ruff>=0.6',
    'mypy>=1.11',
    'django-stubs>=5.0'
)

Write-ProjectFile 'Dockerfile' @(
    'FROM python:3.13.15-slim AS base',
    'ENV PYTHONUNBUFFERED=1',
    'ENV PYTHONDONTWRITEBYTECODE=1',
    'ENV TZ=Europe/Warsaw',
    'WORKDIR /app',
    'RUN apt-get update \',
    ' && apt-get install -y --no-install-recommends curl tzdata \',
    ' && rm -rf /var/lib/apt/lists/*',
    '',
    'FROM base AS dev',
    'COPY requirements.txt requirements-dev.txt ./',
    'RUN pip install --no-cache-dir -r requirements-dev.txt',
    'RUN useradd --create-home --uid 1000 app && chown -R app:app /app',
    'USER app',
    'EXPOSE 8000',
    'CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]',
    '',
    'FROM base AS runtime',
    'COPY requirements.txt ./',
    'RUN pip install --no-cache-dir -r requirements.txt',
    'RUN useradd --create-home --uid 1000 app',
    'COPY --chown=app:app . .',
    'USER app',
    'EXPOSE 8000',
    'CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]'
)

Write-ProjectFile 'compose.yml' @(
    'name: energy-clock',
    '',
    'services:',
    '  redis:',
    '    image: redis:8.8.2-alpine3.23',
    '    restart: unless-stopped',
    '    ports:',
    '      - "127.0.0.1:6381:6379"',
    '    healthcheck:',
    '      test: ["CMD", "redis-cli", "ping"]',
    '      interval: 10s',
    '      timeout: 3s',
    '      retries: 5',
    '',
    '  mailpit:',
    '    image: axllent/mailpit:v1.31.1',
    '    restart: unless-stopped',
    '    ports:',
    '      - "127.0.0.1:8025:8025"',
    '',
    '  web:',
    '    build:',
    '      context: .',
    '      target: dev',
    '    environment:',
    '      DJANGO_SETTINGS_MODULE: config.settings',
    '      DJANGO_SECRET_KEY: ${DJANGO_SECRET_KEY}',
    '      DJANGO_DEBUG: "1"',
    '      REDIS_URL: redis://redis:6379/0',
    '      EMAIL_HOST: mailpit',
    '      EMAIL_PORT: "1025"',
    '      TZ: Europe/Warsaw',
    '    ports:',
    '      - "127.0.0.1:8000:8000"',
    '    volumes:',
    '      - .:/app',
    '    depends_on:',
    '      redis:',
    '        condition: service_healthy',
    '',
    '  worker:',
    '    build:',
    '      context: .',
    '      target: dev',
    '    command: celery -A config worker -l info',
    '    environment:',
    '      DJANGO_SETTINGS_MODULE: config.settings',
    '      DJANGO_SECRET_KEY: ${DJANGO_SECRET_KEY}',
    '      REDIS_URL: redis://redis:6379/0',
    '      TZ: Europe/Warsaw',
    '    volumes:',
    '      - .:/app',
    '    depends_on:',
    '      redis:',
    '        condition: service_healthy',
    '',
    '  beat:',
    '    build:',
    '      context: .',
    '      target: dev',
    '    command: celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler',
    '    environment:',
    '      DJANGO_SETTINGS_MODULE: config.settings',
    '      DJANGO_SECRET_KEY: ${DJANGO_SECRET_KEY}',
    '      REDIS_URL: redis://redis:6379/0',
    '      TZ: Europe/Warsaw',
    '    volumes:',
    '      - .:/app',
    '    depends_on:',
    '      redis:',
    '        condition: service_healthy'
)

Write-ProjectFile 'pyproject.toml' @(
    '[tool.ruff]',
    'line-length = 100',
    'target-version = "py313"',
    '',
    '[tool.ruff.lint]',
    'select = ["E", "F", "I", "N", "UP", "B", "SIM", "RUF", "DTZ"]',
    '',
    '[tool.mypy]',
    'python_version = "3.13"',
    'strict = true',
    'plugins = ["mypy_django_plugin.main"]',
    '',
    '[[tool.mypy.overrides]]',
    'module = ["celery.*", "django_celery_beat.*", "tqdm.*"]',
    'ignore_missing_imports = true',
    '',
    '[tool.django-stubs]',
    'django_settings_module = "config.settings"',
    '',
    '[tool.pytest.ini_options]',
    'DJANGO_SETTINGS_MODULE = "config.settings"',
    'python_files = ["test_*.py"]',
    'testpaths = ["tests"]',
    'addopts = "-m \"not live\""',
    'markers = [',
    '    "live: testy uderzajace w prawdziwe API PSE",',
    ']'
)

Write-ProjectFile 'make.ps1' @(
    'param([Parameter(Position = 0)][string]$Task = "help")',
    '$ErrorActionPreference = "Stop"',
    '',
    '$ComposeBase = @("-p", "energy-clock", "-f", (Join-Path $PSScriptRoot "compose.yml"))',
    '',
    'function Dc  { docker compose @ComposeBase @args }',
    'function Web { Dc run --rm --no-deps web @args }',
    '',
    'switch ($Task) {',
    '    "up"       { Dc up -d --wait redis; Dc up -d mailpit }',
    '    "down"     { Dc down }',
    '    "dev"      { Dc up web worker beat }',
    '    "sh"       { Dc run --rm web bash }',
    '    "logs"     { Dc logs -f web worker beat }',
    '    "migrate"  { Dc run --rm web python manage.py migrate }',
    '    "makemig"  { Dc run --rm web python manage.py makemigrations }',
    '    "super"    { Dc run --rm web python manage.py createsuperuser }',
    '    "shell"    { Dc run --rm web python manage.py shell }',
    '    "test"     { Dc run --rm web pytest }',
    '    "lint"     { Web ruff check . }',
    '    "format"   { Web ruff format . }',
    '    "types"    { Web mypy . }',
    '    "backfill" { Dc run --rm web python manage.py backfill }',
    '    "ps"       { Dc ps }',
    '    "mail"     { Start-Process "http://127.0.0.1:8025" }',
    '    "verify"   { & "$PSScriptRoot/scripts/verify.ps1" }',
    '    "reset"    {',
    '        Write-Host "USUNIE: wolumeny projektu energy-clock oraz data/db.sqlite3" -ForegroundColor Red',
    '        $ok = Read-Host "Na pewno? [t/N]"',
    '        if ($ok -eq "t") {',
    '            Dc down -v',
    '            Remove-Item (Join-Path $PSScriptRoot "data/db.sqlite3") -ErrorAction SilentlyContinue',
    '        } else { Write-Host "Przerwano." }',
    '    }',
    '    "clean"    {',
    '        Write-Host "USUNIE: wolumeny i obrazy lokalne projektu energy-clock" -ForegroundColor Red',
    '        $ok = Read-Host "Na pewno? [t/N]"',
    '        if ($ok -eq "t") { Dc down -v --rmi local } else { Write-Host "Przerwano." }',
    '    }',
    '    default {',
    '        Write-Host "Zadania:" -ForegroundColor Cyan',
    '        Write-Host "  up down dev sh logs ps"',
    '        Write-Host "  migrate makemig super shell"',
    '        Write-Host "  test lint format types"',
    '        Write-Host "  backfill mail verify reset clean"',
    '        Write-Host ""',
    '        Write-Host "Projekt Compose: energy-clock (wymuszony flaga -p)"',
    '        Write-Host "Uzycie: ./make.ps1 dev   albo   make dev"',
    '    }',
    '}'
)

Write-ProjectFile 'make.bat' @(
    '@echo off',
    'setlocal',
    'cd /d "%~dp0"',
    'if "%~1"=="" (',
    '    powershell -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0make.ps1" help',
    ') else (',
    '    powershell -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0make.ps1" %*',
    ')'
)

Write-ProjectFile 'scripts/verify.ps1' @(
    '$ErrorActionPreference = "Continue"',
    '$ProgressPreference = "SilentlyContinue"',
    '$script:failed = 0',
    '',
    'function Test-Item {',
    '    param([string]$Label, [scriptblock]$Check)',
    '    try {',
    '        & $Check *>$null',
    '        if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) {',
    '            Write-Host "  [ok]   $Label" -ForegroundColor Green',
    '            return',
    '        }',
    '    } catch { }',
    '    Write-Host "  [FAIL] $Label" -ForegroundColor Red',
    '    $script:failed++',
    '}',
    '',
    'function Test-Http {',
    '    param([string]$Url)',
    '    try {',
    '        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8 | Out-Null',
    '        $global:LASTEXITCODE = 0',
    '    } catch {',
    '        $global:LASTEXITCODE = 1',
    '    }',
    '}',
    '',
    'Write-Host "Weryfikacja srodowiska:"',
    '',
    '$ComposeBase = @("-p", "energy-clock", "-f", (Join-Path $PSScriptRoot "../compose.yml"))',
    'function Dc { docker compose @ComposeBase @args }',
    '',
    'Test-Item "redis dziala"          { Dc ps --status running redis }',
    'Test-Item "redis odpowiada"       { Dc exec -T redis redis-cli ping }',
    'Test-Item "python w kontenerze"   { Dc run --rm --no-deps web python --version }',
    'Test-Item "django sie laduje"     { Dc run --rm --no-deps web python -c "import django; print(django.get_version())" }',
    'Test-Item "httpx zainstalowany"   { Dc run --rm --no-deps web python -c "import httpx" }',
    'Test-Item "celery zainstalowany"  { Dc run --rm --no-deps web python -c "import celery" }',
    'Test-Item "strefa czasowa PL"     { Dc run --rm --no-deps web python -c "from zoneinfo import ZoneInfo; ZoneInfo(\"Europe/Warsaw\")" }',
    'Test-Item "API PSE odpowiada"     { Dc run --rm --no-deps web curl -fsS --max-time 20 -o /dev/null "https://api.raporty.pse.pl/api/csdac-pln?%24first=1" }',
    'Test-Item "mailpit na 8025"       { Test-Http "http://127.0.0.1:8025" }',
    '',
    '$running = @(Dc ps --services --filter status=running 2>$null)',
    'if ($running -contains "web") {',
    '    Test-Item "django na :8000"   { Test-Http "http://127.0.0.1:8000" }',
    '} else {',
    '    Write-Host "  [--]   web nie uruchomiony - to normalne po bootstrapie" -ForegroundColor Yellow',
    '    Write-Host "         uruchom: ./make.ps1 dev" -ForegroundColor Yellow',
    '}',
    '',
    'Write-Host ""',
    'if ($script:failed -eq 0) {',
    '    Write-Host "Wszystko gotowe." -ForegroundColor Green',
    '} else {',
    '    Write-Host ("Nieudane sprawdzenia: " + $script:failed) -ForegroundColor Red',
    '    exit 1',
    '}'
)

Write-ProjectFile '.devcontainer/devcontainer.json' @(
    '{',
    '  "name": "Energy Clock",',
    '  "dockerComposeFile": "../compose.yml",',
    '  "service": "web",',
    '  "workspaceFolder": "/app",',
    '  "shutdownAction": "stopCompose",',
    '  "overrideCommand": true,',
    '  "forwardPorts": [8000, 8025],',
    '  "customizations": {',
    '    "vscode": {',
    '      "extensions": [',
    '        "ms-python.python",',
    '        "ms-python.vscode-pylance",',
    '        "charliermarsh.ruff",',
    '        "batisteo.vscode-django",',
    '        "ms-azuretools.vscode-docker"',
    '      ]',
    '    }',
    '  }',
    '}'
)

Write-Step 'Zmienne srodowiskowe'

if (Test-Path '.env') {
    Write-Skip '.env (juz istnieje)'
} else {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secret = [Convert]::ToBase64String($bytes)

    $envLines = @(
        ('DJANGO_SECRET_KEY=' + $secret),
        'DJANGO_DEBUG=1',
        'PSE_API_BASE=https://api.raporty.pse.pl/api'
    )
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path (Get-Location).Path '.env'), (($envLines -join "`n") + "`n"), $utf8NoBom)
    Write-Ok '.env (sekret Django wygenerowany)'
}

if (-not (Test-Path '.env.example')) {
    (Get-Content '.env') -replace '=.*', '=' | Set-Content '.env.example' -Encoding utf8
    Write-Ok '.env.example'
}

Write-Step 'Budowanie obrazu'

Invoke-Compose @('build', 'web')
if ($LASTEXITCODE -ne 0) { Write-Fail 'Budowanie obrazu nie powiodlo sie.' }
Write-Ok 'obraz zbudowany'

Write-Step 'Szkielet Django'

if (Test-Path 'manage.py') {
    Write-Skip 'projekt Django juz istnieje'
} else {
    Invoke-Web @('django-admin', 'startproject', 'config', '.')
    Write-Ok 'config/ + manage.py'
}

foreach ($app in @('market', 'planner', 'web')) {
    $initPath = "apps/$app/__init__.py"
    if (-not (Test-Path $initPath)) { New-Item -ItemType File -Path $initPath -Force | Out-Null }

    $className = $app.Substring(0, 1).ToUpper() + $app.Substring(1)
    Write-ProjectFile "apps/$app/apps.py" @(
        'from django.apps import AppConfig',
        '',
        '',
        ('class ' + $className + 'Config(AppConfig):'),
        '    default_auto_field = "django.db.models.BigAutoField"',
        ('    name = "apps.' + $app + '"'),
        ('    label = "' + $app + '"')
    )
}

foreach ($f in @('apps/__init__.py', 'apps/market/migrations/__init__.py',
                 'apps/market/management/__init__.py', 'apps/market/management/commands/__init__.py',
                 'tests/__init__.py')) {
    if (-not (Test-Path $f)) { New-Item -ItemType File -Path $f -Force | Out-Null }
}
Write-Ok 'aplikacje: market, planner, web'

Write-ProjectFile 'config/celery.py' @(
    'import os',
    '',
    'from celery import Celery',
    '',
    'os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")',
    '',
    'app = Celery("energyclock")',
    'app.config_from_object("django.conf:settings", namespace="CELERY")',
    'app.autodiscover_tasks()'
)

Write-ProjectFile 'data/MANIFEST.md' @(
    '# Data manifest',
    '',
    'Dane NIE sa przechowywane w tym repozytorium.',
    '',
    'Zrodlo:   API raportow PSE - https://api.raporty.pse.pl/',
    'Dostep:   publiczny, bez klucza i bez rejestracji',
    '',
    '## Zakres',
    '',
    'Poczatek danych: 2024-06-14 (start polskiego rynku 15-minutowego)',
    'Rozdzielczosc:   15 minut, 96 interwalow na dobe',
    '',
    'UWAGA: w dobe zmiany czasu jest 92 albo 100 interwalow, nie 96.',
    'Zmierzone: 2026-03-29 -> 92, 2025-10-26 -> 100.',
    '',
    '## Endpointy',
    '',
    'csdac-pln     cena rynku dnia nastepnego (PLN/MWh)',
    'pdgobpkd      plan generacji (PV, wiatr, jednostki konwencjonalne)',
    'his-wlk-cal   wykonanie i zapotrzebowanie',
    '',
    '## Jak odtworzyc',
    '',
    '    ./make.ps1 backfill',
    '',
    '## Publikacja',
    '',
    'Ceny na dobe nastepna publikowane sa okolo 14:00 dnia poprzedniego.',
    'Przed publikacja API zwraca pusta liste, nie blad.'
)

Write-Step 'Uruchamianie infrastruktury'

Invoke-Compose @('up', '-d', '--wait', 'redis')
if ($LASTEXITCODE -ne 0) { Write-Fail 'Redis nie wstal.' }
Invoke-Compose @('up', '-d', 'mailpit')
Write-Ok 'redis na 127.0.0.1:6381 (tylko localhost)'
Write-Ok 'mailpit na http://127.0.0.1:8025 (tylko localhost)'

Write-Step 'Weryfikacja'

& (Join-Path (Get-Location).Path 'scripts/verify.ps1')

Write-Host ''
Write-Host '--------------------------------------------------------------' -ForegroundColor Cyan
Write-Host '  Gotowe.' -ForegroundColor Cyan
Write-Host ''
Write-Host '  W PowerShellu:            W cmd.exe:'
Write-Host '  ./make.ps1 dev            make dev       -> http://127.0.0.1:8000'
Write-Host '  ./make.ps1 migrate        make migrate'
Write-Host '  ./make.ps1 test           make test'
Write-Host '  ./make.ps1 backfill       make backfill'
Write-Host '  ./make.ps1 mail           make mail      -> http://127.0.0.1:8025'
Write-Host '  ./make.ps1 verify         make verify'
Write-Host '  ./make.ps1 down           make down'
Write-Host ''
Write-Host '  Nazwa projektu Compose: energy-clock'
Write-Host '  Kazde wywolanie uzywa jawnych flag -p i -f, wiec zmienne'
Write-Host '  COMPOSE_PROJECT_NAME i COMPOSE_FILE z shella nie maja wplywu.'
Write-Host ''
Write-Host '  UWAGA: config/settings.py to nietkniety szablon.'
Write-Host '  INSTALLED_APPS, TIME_ZONE, USE_TZ, Celery i sciezka bazy'
Write-Host '  uzupelnia Claude Code w punkcie 2 promptu.'
Write-Host ''
Write-Host '  Nastepny krok:'
Write-Host '    1) mkdir _local  i skopiuj tam PROMPT_ENERGY_DATA.md'
Write-Host '    2) claude'
Write-Host '--------------------------------------------------------------' -ForegroundColor Cyan
Write-Host ''
