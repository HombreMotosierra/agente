# Agente IA Hibrido

Proyecto de agente local con modo hibrido para trabajar con IA local o proveedor API bajo control del usuario.

## Estado actual

- Interfaz de escritorio en Tkinter.
- Seleccion manual del proveedor: `local` o `api`.
- Interfaz más limpia con panel de ajustes plegable y compositor de mensajes mejorado.
- Ejecucion de herramientas locales para archivos, comandos, voz e integraciones.
- Soporte para Ollama en local y Groq por API.
- Orquestador HTTP operativo con endpoints `/health`, `/ask` y `/accion`.
- Reintentos del modelo y respuestas de respaldo para no quedarse sin contestar fácilmente.
- Panel de `Mejoras` para propuestas seguras que no tocan código automáticamente sin autorización.
- Memoria y habilidades guardadas en SQLite local.

## Estructura

- `Codigo/`: codigo fuente principal.
- `agente_ia_data/`: datos locales de ejecucion, historial y configuracion.
- `iniciar_agente.vbs`: lanzador del agente.

## Objetivo de la siguiente fase

- Seguir separando `core` y `ui` para reducir el tamaño del modulo principal.
- Preparar una base segura para aprendizaje guiado y futuras mejoras del agente.
- Modernizar la interfaz para que se vea mas limpia, consistente y minimalista.
