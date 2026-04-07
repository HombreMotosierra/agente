# Agente IA Hibrido

Proyecto de agente local con modo hibrido para trabajar con IA local o proveedor API bajo control del usuario.

## Estado actual

- Interfaz de escritorio en Tkinter.
- Ejecucion de herramientas locales para archivos, comandos, voz e integraciones.
- Soporte para Ollama en local y Groq por API.
- Memoria y habilidades guardadas en SQLite local.

## Estructura

- `Codigo/`: codigo fuente principal.
- `agente_ia_data/`: datos locales de ejecucion, historial y configuracion.
- `iniciar_agente.vbs`: lanzador del agente.

## Objetivo de la siguiente fase

- Separar `core` y `ui`.
- Mantener seleccion manual de proveedor: local o API.
- Corregir el orquestador para que funcione como servicio real.
- Preparar una base segura para aprendizaje guiado y futuras mejoras del agente.
