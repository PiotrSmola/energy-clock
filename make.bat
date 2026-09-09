@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
    powershell -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0make.ps1" help
) else (
    powershell -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0make.ps1" %*
)
