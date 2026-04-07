@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

where py >nul 2>&1
if errorlevel 1 (
    echo No se encontro el lanzador de Python ^(`py`^).
    echo Instala Python 3.11 o superior para Windows y vuelve a ejecutar este archivo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    py -3.11 -m venv ".venv" 2>nul
    if errorlevel 1 py -3 -m venv ".venv"
)

if not exist ".venv\Scripts\python.exe" (
    echo No se pudo crear el entorno virtual.
    pause
    exit /b 1
)

echo Actualizando pip...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo No se pudo actualizar pip.
    pause
    exit /b 1
)

echo Instalando dependencias Python...
call ".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
if errorlevel 1 (
    echo Fallo la instalacion de dependencias.
    pause
    exit /b 1
)

if not exist "agente_ia_data" mkdir "agente_ia_data"
if not exist "agente_ia_data\config.json" (
    copy /Y "config.example.json" "agente_ia_data\config.json" >nul
)

where ollama >nul 2>&1
if errorlevel 1 (
    echo.
    echo Ollama no esta instalado. El modo local necesitara instalar Ollama aparte.
    echo Puedes seguir usando el modo API si configuras tu clave de Groq en la app.
    echo.
    goto :done
)

echo Verificando modelo local qwen2.5:7b...
ollama list | findstr /I /C:"qwen2.5:7b" >nul
if errorlevel 1 (
    echo Descargando modelo qwen2.5:7b en Ollama...
    ollama pull qwen2.5:7b
)

:done
echo.
echo Instalacion terminada.
echo 1. Ejecuta iniciar_agente.vbs para abrir la app.
echo 2. Si quieres el orquestador HTTP, ejecuta iniciar_orquestador.bat.
pause
