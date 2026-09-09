param([Parameter(Position = 0)][string]$Task = "help")
$ErrorActionPreference = "Stop"

$ComposeBase = @("-p", "energy-clock", "-f", (Join-Path $PSScriptRoot "compose.yml"))

function Dc  { docker compose @ComposeBase @args }
function Web { Dc run --rm --no-deps web @args }

switch ($Task) {
    "up"       { Dc up -d --wait redis; Dc up -d mailpit }
    "down"     { Dc down }
    "dev"      { Dc up web worker beat }
    "sh"       { Dc run --rm web bash }
    "logs"     { Dc logs -f web worker beat }
    "migrate"  { Dc run --rm web python manage.py migrate }
    "makemig"  { Dc run --rm web python manage.py makemigrations }
    "super"    { Dc run --rm web python manage.py createsuperuser }
    "shell"    { Dc run --rm web python manage.py shell }
    "test"     { Dc run --rm web pytest }
    "lint"     { Web ruff check . }
    "format"   { Web ruff format . }
    "types"    { Web mypy . }
    "backfill" { Dc run --rm web python manage.py backfill }
    "ps"       { Dc ps }
    "mail"     { Start-Process "http://127.0.0.1:8025" }
    "verify"   { & "$PSScriptRoot/scripts/verify.ps1" }
    "reset"    {
        Write-Host "USUNIE: wolumeny projektu energy-clock oraz data/db.sqlite3" -ForegroundColor Red
        $ok = Read-Host "Na pewno? [t/N]"
        if ($ok -eq "t") {
            Dc down -v
            Remove-Item (Join-Path $PSScriptRoot "data/db.sqlite3") -ErrorAction SilentlyContinue
        } else { Write-Host "Przerwano." }
    }
    "clean"    {
        Write-Host "USUNIE: wolumeny i obrazy lokalne projektu energy-clock" -ForegroundColor Red
        $ok = Read-Host "Na pewno? [t/N]"
        if ($ok -eq "t") { Dc down -v --rmi local } else { Write-Host "Przerwano." }
    }
    default {
        Write-Host "Zadania:" -ForegroundColor Cyan
        Write-Host "  up down dev sh logs ps"
        Write-Host "  migrate makemig super shell"
        Write-Host "  test lint format types"
        Write-Host "  backfill mail verify reset clean"
        Write-Host ""
        Write-Host "Projekt Compose: energy-clock (wymuszony flaga -p)"
        Write-Host "Uzycie: ./make.ps1 dev   albo   make dev"
    }
}
