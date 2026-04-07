# Agente IA Hibrido

Agente de escritorio para Windows con dos modos de inferencia:

- `local` con Ollama
- `api` con Groq

La aplicacion incluye interfaz Tkinter, memoria local en SQLite, herramientas de archivos y un orquestador HTTP opcional.

## Requisitos

- Windows 10 u 11
- Python 3.11 o superior
- Acceso a internet para instalar dependencias
- Ollama si quieres usar el modo local
- Una API key propia de Groq si quieres usar el modo API

## Instalacion para cualquier persona del equipo

1. Clona o copia esta carpeta completa.
2. Ejecuta `instalar_windows.bat`.
3. Si vas a usar modo local, verifica que Ollama este instalado.
4. Abre `iniciar_agente.vbs`.
5. Si vas a usar modo API, pega tu propia Groq API key desde el boton `Groq Key` o editando `agente_ia_data/config.json`.

## Inicio rapido

- `iniciar_agente.vbs`: abre la interfaz sin mostrar consola.
- `iniciar_agente.bat`: abre la interfaz desde lote.
- `iniciar_orquestador.bat`: levanta el servidor HTTP local.
- `iniciar_orquestador.vbs`: levanta el orquestador ocultando la consola.

## Modo local

El modelo por defecto es `qwen2.5:7b`.

Si Ollama esta instalado, `instalar_windows.bat` intentara descargar ese modelo automaticamente. Si prefieres hacerlo a mano:

```bash
ollama pull qwen2.5:7b
```

## Modo API

La clave no viene incluida. Cada persona debe configurar la suya.

- Proveedor soportado en la app: Groq
- Modelo API por defecto: `llama-3.1-8b-instant`

## Configuracion

- Archivo de ejemplo: `config.example.json`
- Configuracion local real: `agente_ia_data/config.json`

La carpeta `agente_ia_data/` esta ignorada por Git porque guarda historial, ajustes locales y datos personales.

## Estructura

- `Codigo/`: codigo principal de la app y del orquestador.
- `agente_ia_data/`: base de datos local y configuracion de cada persona.
- `requirements.txt`: dependencias Python.
- `instalar_windows.bat`: instalacion automatizada para Windows.

## Notas de uso

- El proveedor no cambia solo: se selecciona manualmente entre `local` y `api`.
- El orquestador expone `/health`, `/ask` y `/accion` en `http://127.0.0.1:8765`.
