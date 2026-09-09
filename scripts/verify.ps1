$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$script:failed = 0

function Test-Item {
    param([string]$Label, [scriptblock]$Check)
    try {
        & $Check *>$null
        if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) {
            Write-Host "  [ok]   $Label" -ForegroundColor Green
            return
        }
    } catch { }
    Write-Host "  [FAIL] $Label" -ForegroundColor Red
    $script:failed++
}

function Test-Http {
    param([string]$Url)
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8 | Out-Null
        $global:LASTEXITCODE = 0
    } catch {
        $global:LASTEXITCODE = 1
    }
}

Write-Host "Weryfikacja srodowiska:"

$ComposeBase = @("-p", "energy-clock", "-f", (Join-Path $PSScriptRoot "../compose.yml"))
function Dc { docker compose @ComposeBase @args }

Test-Item "redis dziala"          { Dc ps --status running redis }
Test-Item "redis odpowiada"       { Dc exec -T redis redis-cli ping }
Test-Item "python w kontenerze"   { Dc run --rm --no-deps web python --version }
Test-Item "django sie laduje"     { Dc run --rm --no-deps web python -c "import django; print(django.get_version())" }
Test-Item "httpx zainstalowany"   { Dc run --rm --no-deps web python -c "import httpx" }
Test-Item "celery zainstalowany"  { Dc run --rm --no-deps web python -c "import celery" }
Test-Item "strefa czasowa PL"     { Dc run --rm --no-deps web python -c "from zoneinfo import ZoneInfo; ZoneInfo(\"Europe/Warsaw\")" }
Test-Item "API PSE odpowiada"     { Dc run --rm --no-deps web curl -fsS --max-time 20 -o /dev/null "https://api.raporty.pse.pl/api/csdac-pln?%24first=1" }
Test-Item "mailpit na 8025"       { Test-Http "http://127.0.0.1:8025" }

$running = @(Dc ps --services --filter status=running 2>$null)
if ($running -contains "web") {
    Test-Item "django na :8000"   { Test-Http "http://127.0.0.1:8000" }
} else {
    Write-Host "  [--]   web nie uruchomiony - to normalne po bootstrapie" -ForegroundColor Yellow
    Write-Host "         uruchom: ./make.ps1 dev" -ForegroundColor Yellow
}

Write-Host ""
if ($script:failed -eq 0) {
    Write-Host "Wszystko gotowe." -ForegroundColor Green
} else {
    Write-Host ("Nieudane sprawdzenia: " + $script:failed) -ForegroundColor Red
    exit 1
}
