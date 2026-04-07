@echo off
setlocal
set "ROOT=%~dp0"

if exist "%ROOT%.venv\Scripts\python.exe" (
    "%ROOT%.venv\Scripts\python.exe" "%ROOT%Codigo\orquestador_local.py"
    exit /b %errorlevel%
)

py -3 "%ROOT%Codigo\orquestador_local.py"
