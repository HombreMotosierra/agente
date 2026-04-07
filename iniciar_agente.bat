@echo off
setlocal
set "ROOT=%~dp0"
set "APP=%ROOT%Codigo\texto.py"

if exist "%ROOT%.venv\Scripts\pythonw.exe" (
    start "" "%ROOT%.venv\Scripts\pythonw.exe" "%APP%"
    exit /b 0
)

where pyw >nul 2>&1
if not errorlevel 1 (
    start "" pyw -3 "%APP%"
    exit /b 0
)

where pythonw >nul 2>&1
if not errorlevel 1 (
    start "" pythonw "%APP%"
    exit /b 0
)

echo No se encontro pythonw. Ejecuta primero instalar_windows.bat.
pause
