import datetime
from difflib import SequenceMatcher
import json
import random
import string
import queue
import re
import sqlite3
import subprocess
import threading
import os
import shutil
import sys
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import ollama
import pyttsx3
import requests
try:
    import speech_recognition as sr
except Exception:
    sr = None


# ========================
# PATHS / CONFIG
# ========================
BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR.parent / "agente_ia_data"
APP_DIR.mkdir(exist_ok=True)

DB_PATH = APP_DIR / "historial.db"
CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "model": "qwen2.5:7b",
    "voz_activa": True,
    "voz_rate": 180,
    "voz_volume": 1.0,
    "voz_voice_id": "",
    "voz_style": "Natural",
    "voz_speed_label": "Normal",
    "voz_entrada_activa": True,
    "voz_entrada_idioma": "es-ES",
    "voz_entrada_microfono": "",
    "modelo_online": "llama-3.1-8b-instant",
    "proveedor_ia": "local",
    "groq_api_key": "",
    "api_base_url": "",
    "api_bearer_token": "",
    "n8n_webhook_url": "",
    "orquestador_url": "http://127.0.0.1:8765",
    "orquestador_token": "",
    "usar_habilidades_auto": True,
    "known_paths": {},
    "window_size": "520x760",
    "compact_window_size": "340x520",
    "window_x": 1000,
    "window_y": 140,
}

DESTRUCTIVE_PATTERNS = [
    r"\bdel\b",
    r"\berase\b",
    r"\brmdir\b",
    r"\bformat\b",
    r"\bremove-item\b",
    r"\bclear-disk\b",
    r"\bdelete\b",
    r"\bdiskpart\b",
    r"rm\s+-rf",
]

NEGATIVE_PATTERNS = (
    "no puedo",
    "no sé",
    "no tengo la capacidad",
    "no puedo acceder",
    "no puedo ayudarte con eso",
)


def cargar_config():
    if not CONFIG_PATH.exists():
        guardar_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)
    return cfg


def guardar_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


CONFIG = cargar_config()
ULTIMA_SOLICITUD_USUARIO = ""
escucha_activa = False


def normalizar_window_size(size):
    if not isinstance(size, str):
        return DEFAULT_CONFIG["window_size"]
    match = re.match(r"^(\d+)x(\d+)$", size.strip())
    if not match:
        return DEFAULT_CONFIG["window_size"]
    w, h = int(match.group(1)), int(match.group(2))
    if w < 320 or h < 420:
        return DEFAULT_CONFIG["window_size"]
    return f"{w}x{h}"


def _obtener_usuario_windows_preferido():
    known = CONFIG.get("known_paths", {}) if isinstance(CONFIG.get("known_paths"), dict) else {}
    user_cfg = str(known.get("windows_user", "")).strip()
    if user_cfg:
        return user_cfg
    return os.environ.get("USERNAME", "") or Path.home().name


def _guardar_known_path(clave, valor):
    if not valor:
        return
    known = CONFIG.get("known_paths")
    if not isinstance(known, dict):
        known = {}
    known[clave] = str(valor)
    CONFIG["known_paths"] = known
    guardar_config(CONFIG)


def descubrir_ruta_escritorio():
    known = CONFIG.get("known_paths", {}) if isinstance(CONFIG.get("known_paths"), dict) else {}
    ruta_cfg = known.get("desktop", "")
    if ruta_cfg:
        p = Path(ruta_cfg)
        if p.exists():
            return p

    candidatos = []
    candidatos.append(Path.home() / "Desktop")
    userprofile = os.environ.get("USERPROFILE", "").strip()
    if userprofile:
        candidatos.append(Path(userprofile) / "Desktop")

    usuario = _obtener_usuario_windows_preferido()
    letras = "CDEFGHIJKLMNOPQRSTUVWXYZ"
    for l in letras:
        root = Path(f"{l}:\\")
        if not root.exists():
            continue
        candidatos.extend(
            [
                root / "Users" / usuario / "Desktop",
                root / "Usuarios" / usuario / "Desktop",
            ]
        )

    for p in candidatos:
        if p.exists() and p.is_dir():
            _guardar_known_path("desktop", str(p))
            _guardar_known_path("windows_user", usuario)
            return p
    return Path.home() / "Desktop"


def _detectar_usuario_desde_texto(texto):
    t = (texto or "").strip()
    if not t:
        return None
    patrones = [
        r"(?i)\bmi\s+usuario\s+es\s+([a-z0-9._-]{2,64})\b",
        r"(?i)\busuario\s*:\s*([a-z0-9._-]{2,64})\b",
        r"(?i)\buser\s*:\s*([a-z0-9._-]{2,64})\b",
    ]
    for pat in patrones:
        m = re.search(pat, t)
        if m:
            return m.group(1)
    return None


def _descubrir_home_windows(usuario=None):
    usuario = (usuario or _obtener_usuario_windows_preferido() or "").strip()
    candidatos = []
    if usuario:
        for l in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = Path(f"{l}:\\")
            if not root.exists():
                continue
            candidatos.extend(
                [
                    root / "Users" / usuario,
                    root / "Usuarios" / usuario,
                ]
            )
    userprofile = os.environ.get("USERPROFILE", "").strip()
    if userprofile:
        candidatos.append(Path(userprofile))
    candidatos.append(Path.home())

    for p in candidatos:
        if p.exists() and p.is_dir():
            _guardar_known_path("windows_user", p.name)
            _guardar_known_path("windows_home", str(p))
            return p
    return Path.home()


CONFIG["window_size"] = normalizar_window_size(CONFIG.get("window_size"))


# ========================
# DB
# ========================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                rol TEXT NOT NULL,
                texto TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS habilidades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_texto TEXT NOT NULL,
                acciones_json TEXT NOT NULL,
                creado_en TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ejecuciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                solicitud TEXT NOT NULL,
                resultado TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS automatizaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                trigger_texto TEXT NOT NULL,
                acciones_json TEXT NOT NULL,
                habilitada INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_consultas (
                cache_key TEXT PRIMARY KEY,
                respuesta TEXT NOT NULL,
                actualizado_en TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lecciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                solicitud TEXT NOT NULL,
                acciones_json TEXT NOT NULL,
                resultado TEXT NOT NULL,
                exito INTEGER NOT NULL
            )
            """
        )
        # Migración desde esquema legado (prompt/respuesta/created_at)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(conversaciones)").fetchall()]
        expected = {"fecha", "rol", "texto"}
        if cols and not expected.issubset(set(cols)):
            legacy = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='conversaciones_legacy'"
            ).fetchone()
            if legacy is None:
                conn.execute("ALTER TABLE conversaciones RENAME TO conversaciones_legacy")
                conn.execute(
                    """
                    CREATE TABLE conversaciones (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fecha TEXT NOT NULL,
                        rol TEXT NOT NULL,
                        texto TEXT NOT NULL
                    )
                    """
                )
                legacy_cols = [r[1] for r in conn.execute("PRAGMA table_info(conversaciones_legacy)").fetchall()]
                if {"prompt", "respuesta", "created_at"}.issubset(set(legacy_cols)):
                    rows = conn.execute(
                        "SELECT created_at, prompt, respuesta FROM conversaciones_legacy ORDER BY id ASC"
                    ).fetchall()
                    for created_at, prompt, respuesta in rows:
                        fecha = created_at or datetime.datetime.now().isoformat()
                        conn.execute(
                            "INSERT INTO conversaciones (fecha, rol, texto) VALUES (?, ?, ?)",
                            (fecha, "user", str(prompt or "")),
                        )
                        conn.execute(
                            "INSERT INTO conversaciones (fecha, rol, texto) VALUES (?, ?, ?)",
                            (fecha, "assistant", str(respuesta or "")),
                        )
        # Migración de cache_consultas desde esquemas previos
        cache_cols = [r[1] for r in conn.execute("PRAGMA table_info(cache_consultas)").fetchall()]
        if cache_cols and "actualizado_en" not in set(cache_cols):
            legacy_cache = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cache_consultas_legacy'"
            ).fetchone()
            if legacy_cache is None:
                conn.execute("ALTER TABLE cache_consultas RENAME TO cache_consultas_legacy")
                conn.execute(
                    """
                    CREATE TABLE cache_consultas (
                        cache_key TEXT PRIMARY KEY,
                        respuesta TEXT NOT NULL,
                        actualizado_en TEXT NOT NULL
                    )
                    """
                )
                legacy_cols = [r[1] for r in conn.execute("PRAGMA table_info(cache_consultas_legacy)").fetchall()]
                # Mapea distintos formatos históricos.
                if {"cache_key", "respuesta", "updated_at"}.issubset(set(legacy_cols)):
                    rows = conn.execute(
                        "SELECT cache_key, respuesta, updated_at FROM cache_consultas_legacy"
                    ).fetchall()
                    for cache_key, respuesta, updated_at in rows:
                        conn.execute(
                            "INSERT OR REPLACE INTO cache_consultas (cache_key, respuesta, actualizado_en) VALUES (?, ?, ?)",
                            (str(cache_key or ""), str(respuesta or ""), str(updated_at or datetime.datetime.now().isoformat())),
                        )
                elif {"clave", "respuesta", "updated_at"}.issubset(set(legacy_cols)):
                    rows = conn.execute(
                        "SELECT clave, respuesta, updated_at FROM cache_consultas_legacy"
                    ).fetchall()
                    for clave, respuesta, updated_at in rows:
                        conn.execute(
                            "INSERT OR REPLACE INTO cache_consultas (cache_key, respuesta, actualizado_en) VALUES (?, ?, ?)",
                            (str(clave or ""), str(respuesta or ""), str(updated_at or datetime.datetime.now().isoformat())),
                        )
        conn.commit()


def guardar_mensaje_db(rol, texto):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO conversaciones (fecha, rol, texto) VALUES (?, ?, ?)",
            (datetime.datetime.now().isoformat(), rol, texto),
        )
        conn.commit()


def obtener_contexto_db(limite=20):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT rol, texto FROM conversaciones ORDER BY id DESC LIMIT ?",
            (limite,),
        ).fetchall()
    rows.reverse()
    return rows


def construir_resumen_contexto(memoria, max_chars=1600):
    if not memoria:
        return ""
    lineas = []
    for rol, txt in memoria:
        r = "Usuario" if str(rol).lower() == "user" else "Asistente"
        t = (txt or "").strip().replace("\n", " ")
        if len(t) > 220:
            t = t[:220] + "..."
        lineas.append(f"- {r}: {t}")
    out = "\n".join(lineas)
    if len(out) > max_chars:
        out = out[-max_chars:]
    return out


def limpiar_historial_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM conversaciones")
        conn.commit()


def normalizar_texto_cache(texto):
    t = (texto or "").lower().strip()
    t = re.sub(r"[^\w\sáéíóúñü]", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def obtener_cache_respuesta(cache_key, max_age_seconds=60):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT respuesta, actualizado_en FROM cache_consultas WHERE cache_key=?",
            (cache_key,),
        ).fetchone()
    if not row:
        return None
    respuesta, actualizado_en = row
    try:
        ts = datetime.datetime.fromisoformat(actualizado_en)
    except ValueError:
        return None
    edad = (datetime.datetime.now() - ts).total_seconds()
    if edad > max_age_seconds:
        return None
    return respuesta


def guardar_cache_respuesta(cache_key, respuesta):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO cache_consultas (cache_key, respuesta, actualizado_en)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                respuesta=excluded.respuesta,
                actualizado_en=excluded.actualizado_en
            """,
            (cache_key, respuesta, datetime.datetime.now().isoformat()),
        )
        conn.commit()


def guardar_habilidad(trigger_texto, acciones):
    trigger_norm = normalizar_texto_cache(trigger_texto)
    acciones_canon = json.dumps(acciones, ensure_ascii=False, sort_keys=True)

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, trigger_texto, acciones_json FROM habilidades ORDER BY id DESC LIMIT 300"
        ).fetchall()
        for hid, trigger_existente, acciones_existente in rows:
            t_exist = normalizar_texto_cache(trigger_existente)
            ratio = SequenceMatcher(None, trigger_norm, t_exist).ratio()
            try:
                parsed_exist = json.loads(acciones_existente)
                parsed_canon = json.dumps(parsed_exist, ensure_ascii=False, sort_keys=True)
            except Exception:
                parsed_canon = acciones_existente or ""
            if ratio >= 0.9 and parsed_canon == acciones_canon:
                conn.execute(
                    "UPDATE habilidades SET trigger_texto=?, creado_en=? WHERE id=?",
                    (
                        trigger_texto.strip()[:200],
                        datetime.datetime.now().isoformat(),
                        hid,
                    ),
                )
                conn.commit()
                return

        conn.execute(
            "INSERT INTO habilidades (trigger_texto, acciones_json, creado_en) VALUES (?, ?, ?)",
            (
                trigger_texto.strip()[:200],
                json.dumps(acciones, ensure_ascii=False),
                datetime.datetime.now().isoformat(),
            ),
        )
        conn.commit()


def buscar_habilidad(texto):
    consulta = normalizar_texto_cache(texto)
    if not consulta:
        return None

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT trigger_texto, acciones_json FROM habilidades ORDER BY id DESC LIMIT 100"
        ).fetchall()

    mejor_accion = None
    mejor_score = 0.0
    for trigger, acciones_json in rows:
        t = normalizar_texto_cache(trigger)
        if not t:
            continue
        palabras = [w for w in re.findall(r"[a-zA-Z0-9áéíóúñ]+", t) if len(w) > 2]
        if not palabras:
            continue
        score = sum(1 for w in palabras if w in consulta)
        ratio = score / max(1, len(palabras))
        # Evita disparos accidentales por coincidencias mínimas.
        if ratio >= 0.75 or t == consulta:
            try:
                return json.loads(acciones_json)
            except json.JSONDecodeError:
                return None
    return None


def respuesta_conversacional_local(texto):
    t = normalizar_texto_cache(texto)
    if not t:
        return None

    usuario = _obtener_usuario_windows_preferido() or "amigo"

    if any(k in t for k in ("sabes quien soy", "quien soy", "quién soy")):
        return f"Sí, te tengo como {usuario}."

    if any(k in t for k in ("hola", "buenas", "que tal", "qué tal")):
        return f"Hola {usuario}. ¿En qué te ayudo?"

    if "que puedes hacer" in t or "qué puedes hacer" in t:
        return (
            "Puedo ayudarte con archivos/rutas en tu equipo (listar, buscar, leer, escribir, mover, copiar), "
            "consultas de fecha y hora, abrir URLs y usar API (modo online/local). "
            "Si quieres, te doy ejemplos concretos."
        )

    if "chiste" in t and "ingenier" in t:
        return (
            "Chiste de ingenieros: \"Funciona en mi máquina\" no es una solución, "
            "es una condición de carrera."
        )

    if "chiste" in t:
        return "Aquí va uno: ¿Cuál es el colmo de un programador? Tener problemas de clase."

    return None


def asegurar_habilidades_base():
    base = [
        ("lista los archivos del escritorio", [{"accion": "listar_directorio", "args": {"path": str(descubrir_ruta_escritorio())}}]),
        ("que elementos tengo en mi escritorio", [{"accion": "listar_directorio", "args": {"path": str(descubrir_ruta_escritorio())}}]),
        ("buscar archivos *.txt en escritorio", [{"accion": "buscar_archivos", "args": {"path": str(descubrir_ruta_escritorio()), "patron": "*.txt"}}]),
        ("abrir google", [{"accion": "abrir_url", "args": {"url": "https://www.google.com"}}]),
    ]
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM habilidades").fetchone()[0]
    if total > 0:
        return
    for trigger, acciones in base:
        guardar_habilidad(trigger, acciones)


def registrar_ejecucion(solicitud, resultado):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO ejecuciones (fecha, solicitud, resultado) VALUES (?, ?, ?)",
            (
                datetime.datetime.now().isoformat(),
                solicitud[:300],
                resultado[:1500],
            ),
        )
        conn.commit()


def registrar_leccion(solicitud, acciones, resultado, exito):
    try:
        acciones_json = json.dumps(acciones or [], ensure_ascii=False)
    except TypeError:
        acciones_json = "[]"
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO lecciones (fecha, solicitud, acciones_json, resultado, exito)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                datetime.datetime.now().isoformat(),
                str(solicitud or "")[:300],
                acciones_json,
                str(resultado or "")[:2000],
                1 if exito else 0,
            ),
        )
        conn.commit()


def obtener_habilidades(limite=300):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """
            SELECT id, trigger_texto, acciones_json, creado_en
            FROM habilidades
            ORDER BY id DESC
            LIMIT ?
            """,
            (limite,),
        ).fetchall()


def actualizar_habilidad(habilidad_id, trigger_texto, acciones_json):
    trigger = (trigger_texto or "").strip()
    if not trigger:
        return False, "El trigger no puede estar vacío."

    try:
        parsed = json.loads(acciones_json)
        if not isinstance(parsed, list):
            return False, "acciones_json debe ser una lista JSON."
    except json.JSONDecodeError as ex:
        return False, f"JSON inválido: {ex}"

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE habilidades SET trigger_texto=?, acciones_json=? WHERE id=?",
            (trigger[:200], json.dumps(parsed, ensure_ascii=False), habilidad_id),
        )
        conn.commit()
    return True, "Habilidad actualizada."


def eliminar_habilidad_id(habilidad_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM habilidades WHERE id=?", (habilidad_id,))
        conn.commit()


def obtener_automatizaciones(limite=300):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """
            SELECT id, nombre, trigger_texto, acciones_json, habilitada, creado_en
            FROM automatizaciones
            ORDER BY id DESC
            LIMIT ?
            """,
            (limite,),
        ).fetchall()


def crear_automatizacion(nombre, trigger_texto, acciones_json, habilitada=True):
    nom = (nombre or "").strip()
    trig = (trigger_texto or "").strip().lower()
    if not nom or not trig:
        return False, "Nombre y trigger son obligatorios."
    try:
        parsed = json.loads(acciones_json)
        if not isinstance(parsed, list):
            return False, "acciones_json debe ser una lista JSON."
    except json.JSONDecodeError as ex:
        return False, f"JSON inválido: {ex}"
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO automatizaciones (nombre, trigger_texto, acciones_json, habilitada, creado_en)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                nom[:120],
                trig[:220],
                json.dumps(parsed, ensure_ascii=False),
                1 if habilitada else 0,
                datetime.datetime.now().isoformat(),
            ),
        )
        conn.commit()
    return True, "Automatización creada."


def actualizar_automatizacion(auto_id, nombre, trigger_texto, acciones_json, habilitada=True):
    nom = (nombre or "").strip()
    trig = (trigger_texto or "").strip().lower()
    if not nom or not trig:
        return False, "Nombre y trigger son obligatorios."
    try:
        parsed = json.loads(acciones_json)
        if not isinstance(parsed, list):
            return False, "acciones_json debe ser una lista JSON."
    except json.JSONDecodeError as ex:
        return False, f"JSON inválido: {ex}"
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE automatizaciones
            SET nombre=?, trigger_texto=?, acciones_json=?, habilitada=?
            WHERE id=?
            """,
            (nom[:120], trig[:220], json.dumps(parsed, ensure_ascii=False), 1 if habilitada else 0, auto_id),
        )
        conn.commit()
    return True, "Automatización actualizada."


def eliminar_automatizacion(auto_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM automatizaciones WHERE id=?", (auto_id,))
        conn.commit()


def borrar_todas_automatizaciones():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM automatizaciones")
        conn.commit()


def buscar_automatizacion_por_trigger(texto):
    t = normalizar_texto_cache(texto)
    if not t:
        return None
    autos = obtener_automatizaciones(limite=500)
    mejor = None
    mejor_score = 0.0
    for _id, nombre, trigger_texto, acciones_json, habilitada, _creado in autos:
        if not habilitada:
            continue
        trigger = normalizar_texto_cache(trigger_texto)
        if not trigger:
            continue
        seq = SequenceMatcher(None, t, trigger).ratio()
        tokens_t = {w for w in re.findall(r"[a-zA-Z0-9áéíóúñ]+", t) if len(w) > 2}
        tokens_tr = {w for w in re.findall(r"[a-zA-Z0-9áéíóúñ]+", trigger) if len(w) > 2}
        overlap = (len(tokens_t & tokens_tr) / max(1, len(tokens_tr))) if tokens_tr else 0.0
        contiene = 1.0 if (trigger in t or t in trigger) else 0.0

        # Solo considerar matches realmente cercanos para evitar desvíos.
        if not (seq >= 0.9 or overlap >= 0.9 or t == trigger):
            continue

        score = max(seq, overlap, contiene)
        try:
            acciones = json.loads(acciones_json)
            if isinstance(acciones, list) and score > mejor_score:
                mejor = {"nombre": nombre, "acciones": acciones}
                mejor_score = score
        except json.JSONDecodeError:
            continue
    return mejor


# ========================
# VOZ ROBUSTA (COLA)
# ========================
voz_queue = queue.Queue()
_voz_lock = threading.Lock()
tts_status_var = None


def set_tts_status(texto):
    if tts_status_var is not None:
        chat_window.after(0, lambda: tts_status_var.set(texto))


def listar_voces_disponibles():
    try:
        eng = pyttsx3.init()
        voces = eng.getProperty("voices") or []
        resultado = []
        for v in voces:
            nombre = getattr(v, "name", "sin_nombre")
            vid = getattr(v, "id", "")
            langs = getattr(v, "languages", [])
            langs_txt = ",".join([str(x) for x in langs]) if langs else ""
            resultado.append({"id": vid, "name": nombre, "langs": langs_txt})
        return resultado
    except Exception:
        return []


def elegir_voz_id(preferencias):
    prefs = [p.lower() for p in preferencias if p]
    voces = listar_voces_disponibles()
    if not voces:
        return ""
    for voz in voces:
        huella = f"{voz['name']} {voz['id']} {voz['langs']}".lower()
        if all(p in huella for p in prefs):
            return voz["id"]
    for voz in voces:
        huella = f"{voz['name']} {voz['id']} {voz['langs']}".lower()
        if any(p in huella for p in prefs):
            return voz["id"]
    return voces[0]["id"]


def aplicar_config_engine(engine):
    try:
        engine.setProperty("rate", int(CONFIG.get("voz_rate", 180)))
    except Exception:
        pass
    try:
        volume = float(CONFIG.get("voz_volume", 1.0))
        volume = max(0.1, min(1.0, volume))
        engine.setProperty("volume", volume)
    except Exception:
        pass
    voz_id = (CONFIG.get("voz_voice_id") or "").strip()
    if voz_id:
        try:
            engine.setProperty("voice", voz_id)
        except Exception:
            pass


def crear_engine():
    eng = pyttsx3.init()
    aplicar_config_engine(eng)
    return eng


def worker_voz():
    engine = crear_engine()
    while True:
        item = voz_queue.get()
        if isinstance(item, tuple):
            texto, evento = item
        else:
            texto, evento = item, None
        if texto is None:
            break
        if not CONFIG.get("voz_activa", True):
            if evento:
                evento.set()
            continue
        try:
            aplicar_config_engine(engine)
            engine.say(texto)
            engine.runAndWait()
        except Exception:
            try:
                engine.stop()
            except Exception:
                pass
            engine = crear_engine()
            # Reintento inmediato una vez.
            try:
                with _voz_lock:
                    set_tts_status("🔊 reintentando...")
                    aplicar_config_engine(engine)
                    engine.say(texto)
                    engine.runAndWait()
            except Exception:
                pass
        finally:
            set_tts_status("🛑 silencio")
            if evento:
                evento.set()


threading.Thread(target=worker_voz, daemon=True).start()


def hablar(texto, esperar=False):
    if not texto.strip():
        return
    voz_queue.put(texto)


def configurar_voz(estilo=None, velocidad=None, volumen=None, genero=None):
    cambios = []

    if velocidad is not None:
        try:
            vel = int(velocidad)
            vel = max(120, min(260, vel))
            CONFIG["voz_rate"] = vel
            cambios.append(f"velocidad={vel}")
        except (TypeError, ValueError):
            return "No pude aplicar velocidad. Usa un número entre 120 y 260."

    if volumen is not None:
        try:
            vol = float(volumen)
            if vol > 1:
                vol = vol / 100.0
            vol = max(0.1, min(1.0, vol))
            CONFIG["voz_volume"] = vol
            cambios.append(f"volumen={int(vol * 100)}%")
        except (TypeError, ValueError):
            return "No pude aplicar volumen. Usa 0.1-1.0 o 10-100."

    preferencias = []
    if genero:
        g = str(genero).lower()
        if g in ("femenina", "mujer", "female"):
            preferencias.extend(["female", "maria", "helena", "zira", "es"])
        elif g in ("masculina", "hombre", "male"):
            preferencias.extend(["male", "david", "pablo", "jorge", "es"])
    if estilo:
        e = str(estilo).lower()
        if "suave" in e or "calida" in e or "cálida" in e:
            preferencias.extend(["female", "es"])
            CONFIG["voz_style"] = "Suave"
        if "profunda" in e or "grave" in e:
            preferencias.extend(["male", "es"])
            CONFIG["voz_style"] = "Profunda"
        if "natural" in e:
            CONFIG["voz_style"] = "Natural"

    if preferencias:
        voice_id = elegir_voz_id(preferencias)
        if voice_id:
            CONFIG["voz_voice_id"] = voice_id
            cambios.append("voz=actualizada")

    guardar_config(CONFIG)
    if not cambios:
        return "No hubo cambios de voz para aplicar."
    return "Voz actualizada en tiempo real: " + ", ".join(cambios)


def aplicar_ajuste_voz_ui(style_label=None, speed_label=None):
    args = {}
    if style_label:
        style_map = {
            "Natural": "natural",
            "Suave": "suave",
            "Profunda": "grave",
            "Femenina": "suave",
            "Masculina": "grave",
        }
        estilo = style_map.get(style_label, "natural")
        args["estilo"] = estilo
        if style_label == "Femenina":
            args["genero"] = "femenina"
        elif style_label == "Masculina":
            args["genero"] = "masculina"
        CONFIG["voz_style"] = style_label

    if speed_label:
        speed_map = {"Lenta": 150, "Normal": 180, "Rápida": 220}
        vel = speed_map.get(speed_label, 180)
        args["velocidad"] = vel
        CONFIG["voz_speed_label"] = speed_label

    if not args:
        return "Sin cambios de voz."
    return configurar_voz(**args)


def listar_microfonos():
    if sr is None:
        return []
    try:
        return sr.Microphone.list_microphone_names()
    except Exception:
        return []


# ========================
# HERRAMIENTAS
# ========================
def normalizar_ruta(path):
    if path is None:
        return Path.home()
    p = str(path).strip().strip("\"'").rstrip("}").rstrip("*").strip()
    p_low = p.lower().replace("\\", "/")
    home_win = _descubrir_home_windows()
    desktop_win = descubrir_ruta_escritorio()
    if "/home/usuario" in p_low or "/home/user" in p_low:
        p = re.sub(r"(?i)/home/(usuario|user)", str(home_win).replace("\\", "/"), p)
    if "/home/" in p_low and ("/desktop" in p_low or "/escritorio" in p_low):
        p = re.sub(r"(?i)/home/[^/]+/(desktop|escritorio)", str(desktop_win).replace("\\", "/"), p)
    if "c:/users/usuario" in p_low or "c:\\users\\usuario" in p.lower():
        p = re.sub(r"(?i)c:[\\/]+users[\\/]+usuario", str(home_win), p)
    if "/path/escritorio" in p_low or "\\path\\escritorio" in p_low:
        p = str(desktop_win)
    if p_low.endswith("/escritorio") or p_low.endswith("\\escritorio"):
        p = str(desktop_win)
    if p.lower().endswith("\\temp") and ("appdata\\local\\temp" in p.lower()):
        return Path(os.environ.get("TEMP", p))
    return Path(p)


def obtener_hora():
    return str(datetime.datetime.now())


def detectar_consulta_fecha_hora(texto):
    t = (texto or "").lower()
    pide_hora = (
        "hora" in t
        or "qué hora" in t
        or "que hora" in t
        or "time" in t
    )
    pide_fecha = (
        "fecha" in t
        or "día" in t
        or "dia" in t
        or "hoy es" in t
        or "date" in t
    )
    if pide_hora and pide_fecha:
        return "fecha_hora"
    if pide_hora:
        return "hora"
    if pide_fecha:
        return "fecha"
    return None


def formatear_fecha_hora_bonita(tipo):
    ahora = datetime.datetime.now()
    dias = [
        "lunes",
        "martes",
        "miércoles",
        "jueves",
        "viernes",
        "sábado",
        "domingo",
    ]
    meses = [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ]
    dia_nombre = dias[ahora.weekday()]
    mes_nombre = meses[ahora.month - 1]
    hora_12 = ahora.strftime("%I:%M:%S")
    ampm = "a. m." if ahora.hour < 12 else "p. m."
    fecha_txt = f"{dia_nombre.capitalize()}, {ahora.day} de {mes_nombre} de {ahora.year}"
    hora_txt = f"{hora_12} {ampm}"

    if tipo == "fecha":
        return f"Hoy es {fecha_txt}."
    if tipo == "hora":
        return f"La hora actual es {hora_txt}."
    return f"Hoy es {fecha_txt} y la hora actual es {hora_txt}."


def detectar_intencion_principal(texto):
    t = normalizar_texto_cache(texto)
    tipo_tiempo = detectar_consulta_fecha_hora(texto)
    if tipo_tiempo:
        return "tiempo"
    if any(k in t for k in ("escritorio", "desktop", "carpeta", "directorio", "archivo", "archivos", "elementos")):
        return "archivos"
    if any(k in t for k in ("api", "token", "tokens", "limite", "límite", "cuota")):
        return "api"
    if any(k in t for k in ("ip", "red", "wifi", "ethernet")):
        return "red"
    if any(k in t for k in ("voz", "microfono", "micrófono", "mic", "habla", "tono")):
        return "voz"
    return "general"


def acciones_permitidas_por_intencion(intencion):
    mapa = {
        "tiempo": {"obtener_hora"},
        "archivos": {
            "listar_directorio",
            "leer_archivo",
            "escribir_archivo",
            "crear_archivo_especifico",
            "editar_archivo",
            "crear_carpeta",
            "eliminar_ruta",
            "mover_ruta",
            "copiar_ruta",
            "buscar_archivos",
            "abrir_ruta",
            "organizar_carpeta",
            "vaciar_carpeta",
        },
        "api": {"llamar_api", "disparar_webhook", "llamar_orquestador"},
        "red": {"obtener_ip_local", "obtener_ip_publica"},
        "voz": {"configurar_voz"},
        "general": set(),
    }
    return mapa.get(intencion, set())


def filtrar_acciones_por_intencion(acciones, texto):
    if not isinstance(acciones, list):
        return []
    intencion = detectar_intencion_principal(texto)
    # Para consultas conversacionales no ejecutamos herramientas.
    if intencion == "general":
        return []
    permitidas = acciones_permitidas_por_intencion(intencion)
    if not permitidas:
        return []
    filtradas = []
    for a in acciones:
        if not isinstance(a, dict):
            continue
        nombre = str(a.get("accion", "")).strip()
        if nombre in permitidas:
            filtradas.append(a)
    return filtradas


def obtener_ip_local():
    return subprocess.getoutput("ipconfig")


def obtener_ip_publica():
    try:
        return requests.get("https://api.ipify.org", timeout=8).text
    except requests.RequestException as ex:
        return f"No disponible: {ex}"


def listar_directorio(path=None):
    destino = normalizar_ruta(path) if path else descubrir_ruta_escritorio()
    try:
        if not destino.exists():
            return f"Ruta no encontrada: {destino}"
        if not destino.is_dir():
            return f"La ruta no es carpeta: {destino}"
        items = sorted(os.listdir(destino))
        if not items:
            return f"La carpeta está vacía: {destino}"
        vista = "\n".join(items[:120])
        return f"Contenido de {destino}:\n{vista}"
    except OSError as ex:
        return f"No se pudo listar {destino}: {ex}"


def leer_archivo(path):
    destino = normalizar_ruta(path)
    try:
        if not destino.exists():
            return f"Ruta no encontrada: {destino}"
        if destino.is_dir():
            return f"La ruta es carpeta, no archivo: {destino}"
        with open(destino, "r", encoding="utf-8", errors="replace") as f:
            contenido = f.read(8000)
        return f"Contenido de {destino}:\n{contenido}"
    except OSError as ex:
        return f"No se pudo leer {destino}: {ex}"


def escribir_archivo(path, contenido="", append=False):
    destino = normalizar_ruta(path)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        modo = "a" if append else "w"
        with open(destino, modo, encoding="utf-8") as f:
            f.write(contenido)
        return f"Archivo guardado: {destino}"
    except OSError as ex:
        return f"No se pudo escribir {destino}: {ex}"


def _asegurar_paquete_python(modulo, paquete_pip=None):
    try:
        __import__(modulo)
        return True, ""
    except ImportError:
        paquete = paquete_pip or modulo
        try:
            subprocess.check_output(
                [sys.executable, "-m", "pip", "install", paquete, "--quiet"],
                stderr=subprocess.STDOUT,
                timeout=120,
                text=True,
            )
            __import__(modulo)
            return True, f"Dependencia instalada: {paquete}"
        except Exception as ex:
            return False, f"No pude instalar {paquete}: {ex}"


def crear_archivo_especifico(path, tipo="txt", contenido=""):
    destino = normalizar_ruta(path)
    tipo_norm = (tipo or destino.suffix.lstrip(".") or "txt").lower()
    if not destino.suffix:
        destino = destino.with_suffix("." + tipo_norm)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return f"No se pudo preparar la carpeta para {destino}: {ex}"

    if tipo_norm in ("txt", "md", "json", "csv", "log", "ini", "py", "js", "html", "css"):
        return escribir_archivo(str(destino), contenido, append=False)

    if tipo_norm == "docx":
        ok, info = _asegurar_paquete_python("docx", "python-docx")
        if not ok:
            return info
        import docx  # type: ignore
        doc = docx.Document()
        doc.add_paragraph(str(contenido or "Documento generado por el agente."))
        doc.save(str(destino))
        return f"Documento Word creado: {destino}"

    if tipo_norm == "xlsx":
        ok, info = _asegurar_paquete_python("openpyxl")
        if not ok:
            return info
        from openpyxl import Workbook  # type: ignore
        wb = Workbook()
        ws = wb.active
        texto = str(contenido or "valor")
        filas = [f for f in texto.splitlines() if f.strip()]
        if not filas:
            filas = [texto]
        for i, fila in enumerate(filas, start=1):
            celdas = [c.strip() for c in fila.split(",")] if "," in fila else [fila]
            for j, celda in enumerate(celdas, start=1):
                ws.cell(row=i, column=j, value=celda)
        wb.save(str(destino))
        return f"Documento Excel creado: {destino}"

    if tipo_norm == "pptx":
        ok, info = _asegurar_paquete_python("pptx", "python-pptx")
        if not ok:
            return info
        from pptx import Presentation  # type: ignore
        prs = Presentation()
        layout = prs.slide_layouts[1] if len(prs.slide_layouts) > 1 else prs.slide_layouts[0]
        slide = prs.slides.add_slide(layout)
        if slide.shapes.title is not None:
            slide.shapes.title.text = "Presentación generada"
        body = str(contenido or "Contenido generado por el agente.")
        if len(slide.placeholders) > 1:
            slide.placeholders[1].text = body
        prs.save(str(destino))
        return f"Presentación PowerPoint creada: {destino}"

    return f"Tipo de archivo no soportado aún: {tipo_norm}. Usa txt, docx, xlsx o pptx."


def editar_archivo(path, contenido="", reemplazar=False):
    destino = normalizar_ruta(path)
    if not destino.exists():
        return f"Ruta no encontrada: {destino}"
    if destino.is_dir():
        return f"La ruta es carpeta, no archivo: {destino}"

    ext = destino.suffix.lower().lstrip(".")
    if ext in ("docx", "xlsx", "pptx"):
        return (
            f"Edición directa de .{ext} aún no soportada sin instrucciones estructuradas. "
            "Pídeme crear una nueva versión del archivo o exportar a txt/csv."
        )
    return escribir_archivo(str(destino), contenido, append=not bool(reemplazar))


def crear_carpeta(path):
    destino = normalizar_ruta(path)
    try:
        destino.mkdir(parents=True, exist_ok=True)
        _guardar_known_path("last_folder", str(destino))
        return f"Carpeta creada/lista: {destino}"
    except OSError as ex:
        return f"No se pudo crear carpeta {destino}: {ex}"


def eliminar_ruta(path):
    destino = normalizar_ruta(path)
    if not destino.exists():
        return f"No existe: {destino}"
    if not confirmar_comando_destructivo(f"eliminar {destino}"):
        return "Eliminación cancelada por el usuario."
    try:
        if destino.is_dir():
            shutil.rmtree(destino)
            return f"Carpeta eliminada: {destino}"
        destino.unlink()
        return f"Archivo eliminado: {destino}"
    except OSError as ex:
        return f"No se pudo eliminar {destino}: {ex}"


def vaciar_carpeta(path):
    base = normalizar_ruta(path)
    if not base.exists() or not base.is_dir():
        return f"No existe carpeta: {base}"
    if not confirmar_comando_destructivo(f"vaciar {base}"):
        return "Vaciado cancelado por el usuario."
    eliminados = 0
    errores = 0
    for item in base.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            eliminados += 1
        except OSError:
            errores += 1
    return f"Vaciado completado en {base}. Eliminados={eliminados}, errores={errores}."


def mover_ruta(origen, destino):
    src = normalizar_ruta(origen)
    dst = normalizar_ruta(destino)
    try:
        if not src.exists():
            return f"No existe origen: {src}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Movido: {src} -> {dst}"
    except OSError as ex:
        return f"No se pudo mover: {ex}"


def copiar_ruta(origen, destino):
    src = normalizar_ruta(origen)
    dst = normalizar_ruta(destino)
    try:
        if not src.exists():
            return f"No existe origen: {src}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        return f"Copiado: {src} -> {dst}"
    except OSError as ex:
        return f"No se pudo copiar: {ex}"


def buscar_archivos(path=None, patron="*"):
    base = normalizar_ruta(path) if path else Path.home()
    try:
        if not base.exists() or not base.is_dir():
            return f"Ruta base inválida: {base}"
        encontrados = [str(p) for p in base.rglob(patron)]
        if not encontrados:
            return f"Sin resultados para '{patron}' en {base}"
        vista = "\n".join(encontrados[:120])
        return f"Resultados en {base} para '{patron}':\n{vista}"
    except OSError as ex:
        return f"No se pudo buscar en {base}: {ex}"


def crear_archivos_aleatorios(path, cantidad=10, prefijo="archivo", extension=".txt", longitud=8):
    base = normalizar_ruta(path)
    try:
        base.mkdir(parents=True, exist_ok=True)
        cant = int(cantidad)
        cant = max(1, min(cant, 1000))
        ext = str(extension or ".txt").strip()
        if not ext.startswith("."):
            ext = "." + ext
        ext = ext[:10]
        longitud = int(longitud)
        longitud = max(4, min(longitud, 24))

        creados = []
        alfabeto = string.ascii_lowercase + string.digits
        for i in range(cant):
            suf = "".join(random.choice(alfabeto) for _ in range(longitud))
            nombre = f"{prefijo}_{i+1}_{suf}{ext}"
            destino = base / nombre
            with open(destino, "w", encoding="utf-8") as f:
                f.write(f"archivo generado automáticamente: {nombre}\n")
            creados.append(nombre)
        preview = "\n".join(creados[:40])
        return f"Creados {len(creados)} archivos en {base}:\n{preview}"
    except Exception as ex:
        return f"No se pudieron crear archivos en {base}: {ex}"


def abrir_ruta(path):
    destino = normalizar_ruta(path)
    if not destino.exists():
        return f"Ruta no encontrada: {destino}"
    try:
        os.startfile(str(destino))
        return f"Abierto: {destino}"
    except OSError as ex:
        return f"No se pudo abrir {destino}: {ex}"


def abrir_url(url):
    try:
        webbrowser.open(url, new=2)
        return f"URL abierta: {url}"
    except Exception as ex:
        return f"No se pudo abrir URL: {ex}"


def llamar_api(url, metodo="GET", json_data=None, data_text=None, headers=None, timeout=30):
    target = (url or "").strip()
    if not target:
        base = (CONFIG.get("api_base_url") or "").strip()
        if not base:
            return "Error: define url o api_base_url."
        target = base
    if not target.startswith("http://") and not target.startswith("https://"):
        return f"Error: URL inválida: {target}"

    method = (metodo or "GET").upper()
    hdrs = {"Content-Type": "application/json"}
    token = (CONFIG.get("api_bearer_token") or "").strip()
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if isinstance(headers, dict):
        for k, v in headers.items():
            hdrs[str(k)] = str(v)

    if "api.example.com" in target.lower():
        return (
            "No tengo configurada tu API real todavía. "
            "En Integraciones define 'API Base URL' y, si aplica, token Bearer."
        )

    try:
        if data_text and json_data is None:
            resp = requests.request(method, target, headers=hdrs, data=str(data_text), timeout=int(timeout))
        else:
            resp = requests.request(method, target, headers=hdrs, json=json_data, timeout=int(timeout))
        txt = resp.text[:5000]
        return f"API {method} {target} -> {resp.status_code}\n{txt}"
    except requests.RequestException as ex:
        return (
            "No pude consultar la API en este momento. "
            "Revisa URL/token en Integraciones y tu conexión de red."
        )


def disparar_webhook(nombre_evento, payload=None):
    hook = (CONFIG.get("n8n_webhook_url") or "").strip()
    if not hook:
        return "Error: falta n8n_webhook_url en configuración."
    data = {"evento": nombre_evento, "payload": payload or {}, "origen": "agente_local"}
    return llamar_api(hook, metodo="POST", json_data=data)


def llamar_orquestador(accion, payload=None):
    base = (CONFIG.get("orquestador_url") or "").strip()
    if not base:
        return "Error: falta orquestador_url en configuración."
    target = base.rstrip("/") + "/accion"
    headers = {"Content-Type": "application/json"}
    token = (CONFIG.get("orquestador_token") or "").strip()
    if token:
        headers["X-Orquestador-Token"] = token
    body = {"accion": accion, "payload": payload or {}}
    try:
        resp = requests.post(target, headers=headers, json=body, timeout=30)
        txt = resp.text[:5000]
        return f"Orquestador {target} -> {resp.status_code}\n{txt}"
    except requests.RequestException as ex:
        return f"Error llamando orquestador: {ex}"


def organizar_carpeta(path=None, excluir_ext=".exe"):
    base = Path(path) if path else Path.home() / "Desktop"
    try:
        if not base.exists() or not base.is_dir():
            return f"Ruta inválida: {base}"

        categorias = {
            "Imagenes": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"},
            "Documentos": {".pdf", ".doc", ".docx", ".txt", ".md", ".xlsx", ".pptx"},
            "Comprimidos": {".zip", ".rar", ".7z", ".tar", ".gz"},
            "Codigo": {".py", ".js", ".ts", ".java", ".cs", ".cpp", ".go", ".html", ".css"},
            "Instaladores": {".msi", ".apk", ".dmg"},
            "Otros": set(),
        }

        movidos = []
        for item in base.iterdir():
            if item.is_dir():
                continue

            ext = item.suffix.lower()
            if excluir_ext and ext == excluir_ext.lower():
                continue

            categoria = "Otros"
            for nombre_cat, exts in categorias.items():
                if exts and ext in exts:
                    categoria = nombre_cat
                    break

            destino_dir = base / categoria
            destino_dir.mkdir(exist_ok=True)
            destino = destino_dir / item.name
            if destino.exists():
                destino = destino_dir / f"{item.stem}_{int(datetime.datetime.now().timestamp())}{item.suffix}"

            shutil.move(str(item), str(destino))
            movidos.append(f"{item.name} -> {categoria}")

        if not movidos:
            return f"No hubo archivos para organizar en {base}."
        resumen = "\n".join(movidos[:120])
        return f"Organización completa en {base} (excluyendo {excluir_ext}).\n{resumen}"
    except OSError as ex:
        return f"No se pudo organizar la carpeta: {ex}"


def comando_requiere_confirmacion(cmd):
    cmd_limpio = (cmd or "").strip().lower()
    return any(re.search(pattern, cmd_limpio) for pattern in DESTRUCTIVE_PATTERNS)


def confirmar_comando_destructivo(cmd):
    resultado = {"ok": False}
    evento = threading.Event()

    def _ask():
        confirmado = messagebox.askyesno(
            "Confirmación requerida",
            (
                "Este comando parece destructivo y podría eliminar datos:\n\n"
                f"{cmd}\n\n"
                "¿Deseas ejecutarlo?"
            ),
            parent=chat_window,
        )
        resultado["ok"] = bool(confirmado)
        evento.set()

    chat_window.after(0, _ask)
    evento.wait()
    return resultado["ok"]


def ejecutar_cmd(cmd=None):
    if not cmd:
        return "Error: faltó parámetro cmd."

    if comando_requiere_confirmacion(cmd):
        if not confirmar_comando_destructivo(cmd):
            return "Comando cancelado por el usuario."

    try:
        out = subprocess.check_output(
            cmd,
            shell=True,
            stderr=subprocess.STDOUT,
            timeout=20,
            text=True,
        )
        return out.strip() or "(sin salida)"
    except subprocess.TimeoutExpired:
        return "Error: el comando excedió el tiempo máximo (20s)."
    except subprocess.CalledProcessError as ex:
        return f"Error ejecutando comando: {ex.output}"


# ========================
# IA
# ========================
def chat_online_groq(prompt_sistema, prompt_usuario):
    api_key = (CONFIG.get("groq_api_key") or "") or os.environ.get("GROQ_API_KEY", "")
    api_key = (
        api_key.replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("\r", "")
        .replace("\n", "")
        .strip()
    )
    if not api_key:
        raise RuntimeError("Falta GROQ_API_KEY. Configúrala en la app o variable de entorno.")
    if not api_key.startswith("gsk_"):
        raise RuntimeError("La API key de Groq no tiene formato válido (debe iniciar con gsk_).")

    model_online = CONFIG.get("modelo_online", "llama-3.1-8b-instant")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_online,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario},
        ],
        "temperature": 0.2,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        if r.status_code >= 400:
            body = ""
            try:
                body = r.json().get("error", {}).get("message", "") or r.text[:240]
            except Exception:
                body = r.text[:240]
            raise RuntimeError(f"Groq {r.status_code}: {body}")
        data = r.json()
    except requests.RequestException as ex:
        raise RuntimeError(f"No se pudo conectar a Groq: {ex}") from ex
    return data["choices"][0]["message"]["content"]


def validar_ollama_disponible():
    base_url = (CONFIG.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=4)
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama respondió {r.status_code}")
    except requests.RequestException as ex:
        raise RuntimeError(
            "Ollama local no está activo. Inícialo con: ollama serve"
        ) from ex


def decidir(prompt):
    memoria = obtener_contexto_db(limite=20)
    historial = construir_resumen_contexto(memoria, max_chars=1800)

    contexto = f"""
Eres un agente conectado a herramientas reales.

Herramientas disponibles:
- obtener_hora
- obtener_ip_local
- obtener_ip_publica
- listar_directorio(path)
- leer_archivo(path)
- escribir_archivo(path, contenido, append)
- crear_archivo_especifico(path, tipo, contenido)
- editar_archivo(path, contenido, reemplazar)
- crear_carpeta(path)
- eliminar_ruta(path)
- mover_ruta(origen, destino)
- copiar_ruta(origen, destino)
- buscar_archivos(path, patron)
- crear_archivos_aleatorios(path, cantidad, prefijo, extension)
- abrir_ruta(path)
- abrir_url(url)
- llamar_api(url, metodo, json_data, data_text, headers, timeout)
- disparar_webhook(nombre_evento, payload)
- llamar_orquestador(accion, payload)
- organizar_carpeta(path, excluir_ext)
- configurar_voz(estilo, velocidad, volumen, genero)
- ejecutar_cmd(cmd)

    Reglas estrictas:
- Responde SIEMPRE JSON válido.
- No inventes acciones.
- Si necesitas herramienta, agrega acciones.
- Si no necesitas herramienta, responde directo con acciones=[].
- Puedes usar comandos del sistema con ejecutar_cmd(cmd).
- Si el comando es destructivo (borrado/format), el sistema pedirá confirmación humana.
    - Nunca respondas "no puedo" o "no sé"; si hay duda, propone acciones de intento.
    - Mantén continuidad: usa el historial para recordar preferencias y contexto reciente.

Formato de salida:
{{
  "respuesta": "texto al usuario",
  "acciones": [
    {{"accion": "nombre_accion", "args": {{}}}}
  ]
}}

Historial reciente:
{historial}

Usuario:
{prompt}
"""

    proveedor = CONFIG.get("proveedor_ia", "local")
    if proveedor == "online":
        return chat_online_groq(
            "Eres un asistente técnico preciso. Debes responder en JSON válido.",
            contexto,
        )

    validar_ollama_disponible()
    res = ollama.chat(
        model=CONFIG["model"],
        messages=[{"role": "user", "content": contexto}],
        keep_alive="30m",
    )
    return res["message"]["content"]


def ejecutar_acciones(lista):
    resultados = []
    for item in lista:
        accion = item.get("accion")
        args = item.get("args", {})

        if accion == "obtener_hora":
            r = obtener_hora()
        elif accion == "obtener_ip_local":
            r = obtener_ip_local()
        elif accion == "obtener_ip_publica":
            r = obtener_ip_publica()
        elif accion == "listar_directorio":
            r = listar_directorio(**args)
        elif accion == "leer_archivo":
            r = leer_archivo(**args)
        elif accion == "escribir_archivo":
            r = escribir_archivo(**args)
        elif accion == "crear_archivo_especifico":
            r = crear_archivo_especifico(**args)
        elif accion == "editar_archivo":
            r = editar_archivo(**args)
        elif accion == "crear_carpeta":
            r = crear_carpeta(**args)
        elif accion == "eliminar_ruta":
            r = eliminar_ruta(**args)
        elif accion == "vaciar_carpeta":
            r = vaciar_carpeta(**args)
        elif accion == "mover_ruta":
            r = mover_ruta(**args)
        elif accion == "copiar_ruta":
            r = copiar_ruta(**args)
        elif accion == "buscar_archivos":
            r = buscar_archivos(**args)
        elif accion == "crear_archivos_aleatorios":
            r = crear_archivos_aleatorios(**args)
        elif accion == "abrir_ruta":
            r = abrir_ruta(**args)
        elif accion == "abrir_url":
            r = abrir_url(**args)
        elif accion == "llamar_api":
            r = llamar_api(**args)
        elif accion == "disparar_webhook":
            r = disparar_webhook(**args)
        elif accion == "llamar_orquestador":
            r = llamar_orquestador(**args)
        elif accion == "organizar_carpeta":
            r = organizar_carpeta(**args)
        elif accion == "configurar_voz":
            r = configurar_voz(**args)
        elif accion == "ejecutar_cmd":
            r = ejecutar_cmd(**args)
        else:
            r = f"Acción no válida: {accion}"

        resultados.append(r)
    return resultados


# ========================
# UI - VENTANA MINIMALISTA
# ========================
root = tk.Tk()
chat_window = root
chat_window.title("Agente IA")
chat_window.geometry(
    f"{CONFIG['window_size']}+{CONFIG.get('window_x', 1000)}+{CONFIG.get('window_y', 140)}"
)
chat_window.configure(bg="#060d1f")
chat_window.minsize(320, 420)

style = ttk.Style()
style.theme_use("clam")
style.configure("TCombobox", fieldbackground="#111827", foreground="white", arrowsize=13)

chat_compacto = False
auto_compact_job = None


def cerrar_app():
    try:
        CONFIG["window_size"] = chat_window.geometry().split("+")[0]
        CONFIG["window_x"] = chat_window.winfo_x()
        CONFIG["window_y"] = chat_window.winfo_y()
        guardar_config(CONFIG)
    finally:
        chat_window.destroy()


# Header minimalista
header = tk.Frame(chat_window, bg="#0f172a", height=48)
header.pack(fill="x")

titulo = tk.Label(
    header,
    text="Asistente IA",
    bg="#0f172a",
    fg="#e2e8f0",
    font=("Segoe UI Semibold", 11),
)
titulo.pack(side="left", padx=10)

estado_var = tk.StringVar(value="Listo")
estado = tk.Label(
    header, textvariable=estado_var, bg="#0f172a", fg="#94a3b8", font=("Segoe UI", 9)
)
estado.pack(side="left")

tts_status_var = tk.StringVar(value="🛑 silencio")
tts_estado = tk.Label(
    header,
    textvariable=tts_status_var,
    bg="#0f172a",
    fg="#60a5fa",
    font=("Segoe UI", 9),
)
tts_estado.pack(side="left", padx=8)

model_var = tk.StringVar(value=CONFIG["model"])
combo_model = ttk.Combobox(
    header,
    textvariable=model_var,
    values=["qwen2.5:7b", "llama3.1:8b", "mistral:7b", "phi3:mini"],
    width=12,
    state="readonly",
)
combo_model.pack(side="right", padx=8, pady=8)

provider_var = tk.StringVar(value=CONFIG.get("proveedor_ia", "local"))
combo_provider = ttk.Combobox(
    header,
    textvariable=provider_var,
    values=["local", "online"],
    width=8,
    state="readonly",
)
combo_provider.pack(side="right", padx=4, pady=8)

online_model_var = tk.StringVar(value=CONFIG.get("modelo_online", "llama-3.1-8b-instant"))
combo_online_model = ttk.Combobox(
    header,
    textvariable=online_model_var,
    values=["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
    width=22,
    state="readonly",
)
combo_online_model.pack(side="right", padx=4, pady=8)

mic_names = listar_microfonos()
mic_var = tk.StringVar(value=CONFIG.get("voz_entrada_microfono", ""))
combo_mic = ttk.Combobox(
    header,
    textvariable=mic_var,
    values=mic_names,
    width=26,
    state="readonly" if mic_names else "disabled",
)
if not mic_var.get() and mic_names:
    mic_var.set(mic_names[0])
combo_mic.pack(side="right", padx=4, pady=8)

voz_style_var = tk.StringVar(value=CONFIG.get("voz_style", "Natural"))
combo_voz_style = ttk.Combobox(
    header,
    textvariable=voz_style_var,
    values=["Natural", "Suave", "Profunda", "Femenina", "Masculina"],
    width=10,
    state="readonly",
)
combo_voz_style.pack(side="right", padx=4, pady=8)

voz_speed_var = tk.StringVar(value=CONFIG.get("voz_speed_label", "Normal"))
combo_voz_speed = ttk.Combobox(
    header,
    textvariable=voz_speed_var,
    values=["Lenta", "Normal", "Rápida"],
    width=8,
    state="readonly",
)
combo_voz_speed.pack(side="right", padx=4, pady=8)

voz_var = tk.BooleanVar(value=CONFIG["voz_activa"])
chk_voz = tk.Checkbutton(
    header,
    text="Voz",
    variable=voz_var,
    command=None,
    bg="#0f172a",
    fg="#cbd5e1",
    selectcolor="#1f2937",
    activebackground="#0f172a",
    activeforeground="#cbd5e1",
    font=("Segoe UI", 9),
)
chk_voz.pack(side="right")

skills_auto_var = tk.BooleanVar(value=bool(CONFIG.get("usar_habilidades_auto", False)))
chk_skills_auto = tk.Checkbutton(
    header,
    text="AutoSkills",
    variable=skills_auto_var,
    bg="#0f172a",
    fg="#cbd5e1",
    selectcolor="#1f2937",
    activebackground="#0f172a",
    activeforeground="#cbd5e1",
    font=("Segoe UI", 9),
)
chk_skills_auto.pack(side="right", padx=4)


def on_voice_ui_change(_event=None):
    msg = aplicar_ajuste_voz_ui(
        style_label=voz_style_var.get(),
        speed_label=voz_speed_var.get(),
    )
    estado_var.set("Voz actualizada")
    guardar_config(CONFIG)
    chat_window.after(1200, lambda: estado_var.set("Listo"))
    return msg


def on_provider_change(_event=None):
    CONFIG["proveedor_ia"] = provider_var.get()
    CONFIG["modelo_online"] = online_model_var.get()
    guardar_config(CONFIG)
    estado_var.set(f"Proveedor: {CONFIG['proveedor_ia']}")
    chat_window.after(1200, lambda: estado_var.set("Listo"))


def on_toggle_voz():
    CONFIG["voz_activa"] = bool(voz_var.get())
    guardar_config(CONFIG)
    if CONFIG["voz_activa"]:
        set_tts_status("🛑 silencio")
    else:
        set_tts_status("🔇 voz desactivada")


chk_voz.configure(command=on_toggle_voz)


def alternar_compacto():
    global chat_compacto
    chat_compacto = not chat_compacto
    if chat_compacto:
        chat_window.geometry(
            f"{CONFIG['compact_window_size']}+{chat_window.winfo_x()}+{chat_window.winfo_y()}"
        )
        btn_compacto.configure(text="□")
    else:
        chat_window.geometry(
            f"{CONFIG['window_size']}+{chat_window.winfo_x()}+{chat_window.winfo_y()}"
        )
        btn_compacto.configure(text="▣")


btn_compacto = tk.Button(
    header,
    text="▣",
    command=alternar_compacto,
    bg="#111827",
    fg="#cbd5e1",
    relief="flat",
    padx=7,
)
btn_compacto.pack(side="right", padx=4)



def guardar_preferencias():
    CONFIG["model"] = model_var.get()
    CONFIG["voz_activa"] = bool(voz_var.get())
    CONFIG["window_size"] = chat_window.geometry().split("+")[0]
    CONFIG["window_x"] = chat_window.winfo_x()
    CONFIG["window_y"] = chat_window.winfo_y()
    guardar_config(CONFIG)
    estado_var.set("Guardado")
    if not CONFIG["voz_activa"]:
        set_tts_status("🔇 voz desactivada")
    else:
        set_tts_status("🛑 silencio")
    chat_window.after(1200, lambda: estado_var.set("Listo"))


def abrir_panel_habilidades():
    panel = tk.Toplevel(chat_window)
    panel.title("Habilidades aprendidas")
    panel.geometry("880x560")
    panel.configure(bg="#030712")

    top = tk.Frame(panel, bg="#0f172a")
    top.pack(fill="x")

    tk.Label(
        top,
        text="Panel de habilidades aprendidas",
        bg="#0f172a",
        fg="#e2e8f0",
        font=("Segoe UI Semibold", 11),
    ).pack(side="left", padx=10, pady=8)

    body = tk.Frame(panel, bg="#030712")
    body.pack(fill="both", expand=True, padx=10, pady=10)

    columns = ("id", "trigger", "creado")
    tree = ttk.Treeview(body, columns=columns, show="headings", height=14)
    tree.heading("id", text="ID")
    tree.heading("trigger", text="Trigger")
    tree.heading("creado", text="Creado")
    tree.column("id", width=60, anchor="center")
    tree.column("trigger", width=480, anchor="w")
    tree.column("creado", width=220, anchor="w")
    tree.pack(fill="x")

    tk.Label(
        body,
        text="Trigger",
        bg="#030712",
        fg="#cbd5e1",
        font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(10, 2))
    trigger_entry = tk.Entry(
        body, bg="#111827", fg="#e2e8f0", insertbackground="#e2e8f0", relief="flat"
    )
    trigger_entry.pack(fill="x", ipady=5)

    tk.Label(
        body,
        text="Acciones JSON",
        bg="#030712",
        fg="#cbd5e1",
        font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(10, 2))
    acciones_txt = tk.Text(
        body,
        bg="#0b1220",
        fg="#e2e8f0",
        insertbackground="#e2e8f0",
        relief="flat",
        height=12,
    )
    acciones_txt.pack(fill="both", expand=True)

    selected_id = {"value": None}

    def cargar_tabla():
        for item in tree.get_children():
            tree.delete(item)
        for hid, trigger, _acciones, creado in obtener_habilidades():
            tree.insert("", "end", values=(hid, trigger, creado))

    def on_select(_evt=None):
        sel = tree.selection()
        if not sel:
            return
        vals = tree.item(sel[0], "values")
        if not vals:
            return
        hid = int(vals[0])
        selected_id["value"] = hid
        filas = [r for r in obtener_habilidades() if int(r[0]) == hid]
        if not filas:
            return
        _, trigger, acciones_json, _ = filas[0]
        trigger_entry.delete(0, tk.END)
        trigger_entry.insert(0, trigger)
        acciones_txt.delete("1.0", tk.END)
        acciones_txt.insert("1.0", acciones_json)

    tree.bind("<<TreeviewSelect>>", on_select)

    acciones_botones = tk.Frame(body, bg="#030712")
    acciones_botones.pack(fill="x", pady=(8, 0))

    def guardar_edicion():
        if not selected_id["value"]:
            messagebox.showinfo("Editar habilidad", "Selecciona una habilidad primero.", parent=panel)
            return
        ok, msg = actualizar_habilidad(
            selected_id["value"],
            trigger_entry.get(),
            acciones_txt.get("1.0", tk.END).strip(),
        )
        if not ok:
            messagebox.showerror("Editar habilidad", msg, parent=panel)
            return
        messagebox.showinfo("Editar habilidad", msg, parent=panel)
        cargar_tabla()

    def borrar_habilidad():
        if not selected_id["value"]:
            messagebox.showinfo("Eliminar habilidad", "Selecciona una habilidad primero.", parent=panel)
            return
        if not messagebox.askyesno(
            "Eliminar habilidad",
            "¿Seguro que deseas eliminar esta habilidad?",
            parent=panel,
        ):
            return
        eliminar_habilidad_id(selected_id["value"])
        selected_id["value"] = None
        trigger_entry.delete(0, tk.END)
        acciones_txt.delete("1.0", tk.END)
        cargar_tabla()

    tk.Button(
        acciones_botones,
        text="Recargar",
        command=cargar_tabla,
        bg="#111827",
        fg="#cbd5e1",
        relief="flat",
        padx=10,
    ).pack(side="left")
    tk.Button(
        acciones_botones,
        text="Guardar cambios",
        command=guardar_edicion,
        bg="#1d4ed8",
        fg="#f8fafc",
        relief="flat",
        padx=10,
    ).pack(side="left", padx=6)
    tk.Button(
        acciones_botones,
        text="Eliminar",
        command=borrar_habilidad,
        bg="#7f1d1d",
        fg="#f8fafc",
        relief="flat",
        padx=10,
    ).pack(side="left")
    tk.Button(
        acciones_botones,
        text="Eliminar todas",
        command=lambda: borrar_todas_habilidades(),
        bg="#991b1b",
        fg="#f8fafc",
        relief="flat",
        padx=10,
    ).pack(side="left", padx=6)

    def borrar_todas_habilidades():
        if not messagebox.askyesno(
            "Eliminar todas",
            "¿Seguro que deseas eliminar TODAS las habilidades aprendidas?",
            parent=panel,
        ):
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM habilidades")
            conn.commit()
        selected_id["value"] = None
        trigger_entry.delete(0, tk.END)
        acciones_txt.delete("1.0", tk.END)
        cargar_tabla()

    cargar_tabla()


def pedir_api_key_groq():
    panel = tk.Toplevel(chat_window)
    panel.title("Configurar Groq API Key")
    panel.geometry("560x170")
    panel.configure(bg="#030712")

    tk.Label(
        panel,
        text="Pega tu API key de Groq:",
        bg="#030712",
        fg="#e2e8f0",
        font=("Segoe UI", 10),
    ).pack(anchor="w", padx=12, pady=(12, 6))

    key_entry = tk.Entry(
        panel,
        show="*",
        bg="#111827",
        fg="#e2e8f0",
        insertbackground="#e2e8f0",
        relief="flat",
        font=("Segoe UI", 10),
    )
    key_entry.pack(fill="x", padx=12, ipady=5)
    key_entry.insert(0, CONFIG.get("groq_api_key", ""))

    def guardar_key():
        raw = key_entry.get() or ""
        key_clean = (
            raw.replace("\ufeff", "")
            .replace("\u200b", "")
            .replace("\r", "")
            .replace("\n", "")
            .strip()
        )
        CONFIG["groq_api_key"] = key_clean
        guardar_config(CONFIG)
        try:
            # Validación en caliente para evitar guardar una key rota sin avisar.
            chat_online_groq(
                "Valida conectividad y responde JSON.",
                'Responde EXACTO: {"respuesta":"ok","acciones":[]}',
            )
            estado_var.set("Groq conectado")
            chat_window.after(1400, lambda: estado_var.set("Listo"))
            panel.destroy()
        except Exception as ex:
            messagebox.showerror("Groq API Key", f"Key guardada pero inválida/no usable:\n{ex}", parent=panel)
            estado_var.set("Error en key Groq")
            chat_window.after(1800, lambda: estado_var.set("Listo"))

    tk.Button(
        panel,
        text="Guardar key",
        command=guardar_key,
        bg="#1d4ed8",
        fg="#f8fafc",
        relief="flat",
        padx=12,
    ).pack(anchor="e", padx=12, pady=12)


def abrir_panel_integraciones():
    panel = tk.Toplevel(chat_window)
    panel.title("Integraciones API / Webhook")
    panel.geometry("760x320")
    panel.configure(bg="#030712")

    body = tk.Frame(panel, bg="#030712")
    body.pack(fill="both", expand=True, padx=12, pady=12)

    tk.Label(body, text="API base URL", bg="#030712", fg="#e2e8f0", font=("Segoe UI", 10)).pack(anchor="w")
    api_url = tk.Entry(body, bg="#111827", fg="#e2e8f0", insertbackground="#e2e8f0", relief="flat")
    api_url.pack(fill="x", ipady=5, pady=(2, 8))
    api_url.insert(0, CONFIG.get("api_base_url", ""))

    tk.Label(body, text="API Bearer token", bg="#030712", fg="#e2e8f0", font=("Segoe UI", 10)).pack(anchor="w")
    api_token = tk.Entry(
        body, show="*", bg="#111827", fg="#e2e8f0", insertbackground="#e2e8f0", relief="flat"
    )
    api_token.pack(fill="x", ipady=5, pady=(2, 8))
    api_token.insert(0, CONFIG.get("api_bearer_token", ""))

    tk.Label(body, text="n8n Webhook URL", bg="#030712", fg="#e2e8f0", font=("Segoe UI", 10)).pack(anchor="w")
    hook_url = tk.Entry(body, bg="#111827", fg="#e2e8f0", insertbackground="#e2e8f0", relief="flat")
    hook_url.pack(fill="x", ipady=5, pady=(2, 12))
    hook_url.insert(0, CONFIG.get("n8n_webhook_url", ""))

    tk.Label(body, text="Orquestador URL", bg="#030712", fg="#e2e8f0", font=("Segoe UI", 10)).pack(anchor="w")
    orch_url = tk.Entry(body, bg="#111827", fg="#e2e8f0", insertbackground="#e2e8f0", relief="flat")
    orch_url.pack(fill="x", ipady=5, pady=(2, 8))
    orch_url.insert(0, CONFIG.get("orquestador_url", "http://127.0.0.1:8765"))

    tk.Label(body, text="Orquestador token", bg="#030712", fg="#e2e8f0", font=("Segoe UI", 10)).pack(anchor="w")
    orch_token = tk.Entry(
        body, show="*", bg="#111827", fg="#e2e8f0", insertbackground="#e2e8f0", relief="flat"
    )
    orch_token.pack(fill="x", ipady=5, pady=(2, 12))
    orch_token.insert(0, CONFIG.get("orquestador_token", ""))

    def guardar_integraciones():
        CONFIG["api_base_url"] = api_url.get().strip()
        CONFIG["api_bearer_token"] = api_token.get().strip()
        CONFIG["n8n_webhook_url"] = hook_url.get().strip()
        CONFIG["orquestador_url"] = orch_url.get().strip()
        CONFIG["orquestador_token"] = orch_token.get().strip()
        guardar_config(CONFIG)
        estado_var.set("Integraciones guardadas")
        chat_window.after(1200, lambda: estado_var.set("Listo"))
        panel.destroy()

    def probar_api():
        res = llamar_api("", metodo="GET")
        messagebox.showinfo("Prueba API", res[:2000], parent=panel)

    def probar_webhook():
        res = disparar_webhook("prueba_manual", {"ok": True})
        messagebox.showinfo("Prueba Webhook", res[:2000], parent=panel)

    def probar_orquestador():
        res = llamar_orquestador("health", {})
        messagebox.showinfo("Prueba Orquestador", res[:2000], parent=panel)

    foot = tk.Frame(body, bg="#030712")
    foot.pack(fill="x")
    tk.Button(
        foot, text="Probar API", command=probar_api, bg="#111827", fg="#cbd5e1", relief="flat", padx=10
    ).pack(side="left")
    tk.Button(
        foot, text="Probar Webhook", command=probar_webhook, bg="#111827", fg="#cbd5e1", relief="flat", padx=10
    ).pack(side="left", padx=6)
    tk.Button(
        foot, text="Probar Orquestador", command=probar_orquestador, bg="#111827", fg="#cbd5e1", relief="flat", padx=10
    ).pack(side="left")
    tk.Button(
        foot, text="Guardar", command=guardar_integraciones, bg="#1d4ed8", fg="#f8fafc", relief="flat", padx=12
    ).pack(side="right")


def abrir_panel_automatizaciones():
    panel = tk.Toplevel(chat_window)
    panel.title("Automatizaciones")
    panel.geometry("980x620")
    panel.configure(bg="#030712")

    top = tk.Frame(panel, bg="#0f172a")
    top.pack(fill="x")
    tk.Label(
        top,
        text="Automatizaciones (disparadores por texto)",
        bg="#0f172a",
        fg="#e2e8f0",
        font=("Segoe UI Semibold", 11),
    ).pack(side="left", padx=10, pady=8)

    body = tk.Frame(panel, bg="#030712")
    body.pack(fill="both", expand=True, padx=10, pady=10)

    columns = ("id", "nombre", "trigger", "estado", "creado")
    tree = ttk.Treeview(body, columns=columns, show="headings", height=14)
    tree.heading("id", text="ID")
    tree.heading("nombre", text="Nombre")
    tree.heading("trigger", text="Trigger")
    tree.heading("estado", text="Estado")
    tree.heading("creado", text="Creado")
    tree.column("id", width=50, anchor="center")
    tree.column("nombre", width=170, anchor="w")
    tree.column("trigger", width=320, anchor="w")
    tree.column("estado", width=80, anchor="center")
    tree.column("creado", width=220, anchor="w")
    tree.pack(fill="x")

    form = tk.Frame(body, bg="#030712")
    form.pack(fill="both", expand=True, pady=(10, 0))

    tk.Label(form, text="Nombre", bg="#030712", fg="#cbd5e1", font=("Segoe UI", 9)).pack(anchor="w")
    nombre_entry = tk.Entry(form, bg="#111827", fg="#e2e8f0", insertbackground="#e2e8f0", relief="flat")
    nombre_entry.pack(fill="x", ipady=5)

    tk.Label(form, text="Trigger (texto)", bg="#030712", fg="#cbd5e1", font=("Segoe UI", 9)).pack(
        anchor="w", pady=(8, 2)
    )
    trigger_entry = tk.Entry(form, bg="#111827", fg="#e2e8f0", insertbackground="#e2e8f0", relief="flat")
    trigger_entry.pack(fill="x", ipady=5)

    habilitada_var = tk.BooleanVar(value=True)
    tk.Checkbutton(
        form,
        text="Habilitada",
        variable=habilitada_var,
        bg="#030712",
        fg="#cbd5e1",
        selectcolor="#1f2937",
        activebackground="#030712",
        activeforeground="#cbd5e1",
        font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(8, 2))

    tk.Label(form, text="Acciones JSON", bg="#030712", fg="#cbd5e1", font=("Segoe UI", 9)).pack(anchor="w")
    acciones_txt = tk.Text(
        form,
        bg="#0b1220",
        fg="#e2e8f0",
        insertbackground="#e2e8f0",
        relief="flat",
        height=12,
    )
    acciones_txt.pack(fill="both", expand=True)

    selected_id = {"value": None}

    def limpiar_form():
        selected_id["value"] = None
        nombre_entry.delete(0, tk.END)
        trigger_entry.delete(0, tk.END)
        acciones_txt.delete("1.0", tk.END)
        habilitada_var.set(True)

    def cargar_tabla():
        for item in tree.get_children():
            tree.delete(item)
        for aid, nombre, trigger, _acciones, habilitada, creado in obtener_automatizaciones():
            estado = "ON" if int(habilitada) == 1 else "OFF"
            tree.insert("", "end", values=(aid, nombre, trigger, estado, creado))

    def on_select(_evt=None):
        sel = tree.selection()
        if not sel:
            return
        vals = tree.item(sel[0], "values")
        if not vals:
            return
        aid = int(vals[0])
        filas = [r for r in obtener_automatizaciones() if int(r[0]) == aid]
        if not filas:
            return
        _id, nombre, trigger, acciones_json, habilitada, _creado = filas[0]
        selected_id["value"] = _id
        nombre_entry.delete(0, tk.END)
        nombre_entry.insert(0, nombre)
        trigger_entry.delete(0, tk.END)
        trigger_entry.insert(0, trigger)
        acciones_txt.delete("1.0", tk.END)
        acciones_txt.insert("1.0", acciones_json)
        habilitada_var.set(bool(habilitada))

    tree.bind("<<TreeviewSelect>>", on_select)

    botones = tk.Frame(form, bg="#030712")
    botones.pack(fill="x", pady=(8, 0))

    def crear_auto():
        ok, msg = crear_automatizacion(
            nombre_entry.get(),
            trigger_entry.get(),
            acciones_txt.get("1.0", tk.END).strip(),
            habilitada=habilitada_var.get(),
        )
        if not ok:
            messagebox.showerror("Automatizaciones", msg, parent=panel)
            return
        cargar_tabla()
        limpiar_form()
        messagebox.showinfo("Automatizaciones", msg, parent=panel)

    def guardar_auto():
        if not selected_id["value"]:
            messagebox.showinfo("Automatizaciones", "Selecciona una automatización primero.", parent=panel)
            return
        ok, msg = actualizar_automatizacion(
            selected_id["value"],
            nombre_entry.get(),
            trigger_entry.get(),
            acciones_txt.get("1.0", tk.END).strip(),
            habilitada=habilitada_var.get(),
        )
        if not ok:
            messagebox.showerror("Automatizaciones", msg, parent=panel)
            return
        cargar_tabla()
        messagebox.showinfo("Automatizaciones", msg, parent=panel)

    def eliminar_auto():
        if not selected_id["value"]:
            messagebox.showinfo("Automatizaciones", "Selecciona una automatización primero.", parent=panel)
            return
        if not messagebox.askyesno(
            "Automatizaciones",
            "¿Seguro que deseas eliminar esta automatización?",
            parent=panel,
        ):
            return
        eliminar_automatizacion(selected_id["value"])
        cargar_tabla()
        limpiar_form()

    def eliminar_todas():
        if not messagebox.askyesno(
            "Automatizaciones",
            "¿Eliminar TODAS las automatizaciones?",
            parent=panel,
        ):
            return
        borrar_todas_automatizaciones()
        cargar_tabla()
        limpiar_form()

    tk.Button(
        botones,
        text="Recargar",
        command=cargar_tabla,
        bg="#111827",
        fg="#cbd5e1",
        relief="flat",
        padx=10,
    ).pack(side="left")
    tk.Button(
        botones,
        text="Nueva",
        command=crear_auto,
        bg="#0f766e",
        fg="#f8fafc",
        relief="flat",
        padx=10,
    ).pack(side="left", padx=6)
    tk.Button(
        botones,
        text="Guardar cambios",
        command=guardar_auto,
        bg="#1d4ed8",
        fg="#f8fafc",
        relief="flat",
        padx=10,
    ).pack(side="left")
    tk.Button(
        botones,
        text="Eliminar",
        command=eliminar_auto,
        bg="#7f1d1d",
        fg="#f8fafc",
        relief="flat",
        padx=10,
    ).pack(side="left", padx=6)
    tk.Button(
        botones,
        text="Eliminar todas",
        command=eliminar_todas,
        bg="#991b1b",
        fg="#f8fafc",
        relief="flat",
        padx=10,
    ).pack(side="left")

    cargar_tabla()


btn_guardar = tk.Button(
    header,
    text="Guardar",
    command=guardar_preferencias,
    bg="#1d4ed8",
    fg="#f8fafc",
    relief="flat",
    padx=10,
)
btn_guardar.pack(side="right", padx=6)

btn_habilidades = tk.Button(
    header,
    text="Habilidades",
    command=abrir_panel_habilidades,
    bg="#111827",
    fg="#cbd5e1",
    relief="flat",
    padx=10,
)
btn_habilidades.pack(side="right", padx=6)

btn_automatizaciones = tk.Button(
    header,
    text="Automatizaciones",
    command=abrir_panel_automatizaciones,
    bg="#111827",
    fg="#cbd5e1",
    relief="flat",
    padx=10,
)
btn_automatizaciones.pack(side="right", padx=6)

btn_integraciones = tk.Button(
    header,
    text="Integraciones",
    command=abrir_panel_integraciones,
    bg="#111827",
    fg="#cbd5e1",
    relief="flat",
    padx=10,
)
btn_integraciones.pack(side="right", padx=6)

btn_key = tk.Button(
    header,
    text="Groq Key",
    command=pedir_api_key_groq,
    bg="#111827",
    fg="#cbd5e1",
    relief="flat",
    padx=10,
)
btn_key.pack(side="right", padx=6)

frame_chat = tk.Frame(chat_window, bg="#030712")
frame_chat.pack(fill="both", expand=True)

canvas = tk.Canvas(frame_chat, bg="#030712", highlightthickness=0)
scrollbar = ttk.Scrollbar(
    frame_chat, orient="vertical", style="Vertical.TScrollbar", command=canvas.yview
)
scrollable_frame = tk.Frame(canvas, bg="#030712")

scrollable_frame.bind(
    "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
)
canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
canvas.configure(yscrollcommand=scrollbar.set)
canvas.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")


def mensaje(texto, tipo):
    frame = tk.Frame(scrollable_frame, bg="#030712")

    if tipo == "user":
        color = "#1d4ed8"
        anchor = "e"
    elif tipo == "system":
        color = "#334155"
        anchor = "w"
    else:
        color = "#0f172a"
        anchor = "w"

    bubble = tk.Label(
        frame,
        text=texto,
        bg=color,
        fg="white",
        wraplength=340,
        padx=12,
        pady=9,
        justify="left",
        font=("Segoe UI", 10),
    )
    bubble.pack(anchor=anchor, padx=12, pady=6)
    frame.pack(fill="both")

    chat_window.update_idletasks()
    canvas.yview_moveto(1)


frame_input = tk.Frame(chat_window, bg="#030712")
frame_input.pack(fill="x")

entrada = tk.Entry(
    frame_input,
    bg="#111827",
    fg="#e2e8f0",
    insertbackground="#e2e8f0",
    font=("Segoe UI", 11),
    relief="flat",
)
entrada.pack(side="left", fill="x", expand=True, padx=10, pady=10, ipady=5)


def guardar_geometria_actual():
    CONFIG["window_size"] = normalizar_window_size(chat_window.geometry().split("+")[0])
    CONFIG["window_x"] = chat_window.winfo_x()
    CONFIG["window_y"] = chat_window.winfo_y()
    guardar_config(CONFIG)


def programar_auto_compacto():
    global auto_compact_job
    if auto_compact_job:
        chat_window.after_cancel(auto_compact_job)
    auto_compact_job = chat_window.after(18000, lambda: compactar_si_inactivo())


def compactar_si_inactivo():
    global chat_compacto, auto_compact_job
    auto_compact_job = None
    if chat_window.focus_displayof() is None and not chat_compacto:
        chat_compacto = True
        chat_window.geometry(
            f"{CONFIG['compact_window_size']}+{chat_window.winfo_x()}+{chat_window.winfo_y()}"
        )
        btn_compacto.configure(text="□")
        guardar_geometria_actual()


def expandir_si_compacto():
    global chat_compacto
    if chat_compacto:
        chat_compacto = False
        chat_window.geometry(
            f"{CONFIG['window_size']}+{chat_window.winfo_x()}+{chat_window.winfo_y()}"
        )
        btn_compacto.configure(text="▣")
    programar_auto_compacto()


def limpiar_historial():
    limpiar_historial_db()
    for w in scrollable_frame.winfo_children():
        w.destroy()
    mensaje("Historial limpiado.", "system")


btn_limpiar = tk.Button(
    frame_input,
    text="Limpiar",
    command=limpiar_historial,
    bg="#111827",
    fg="#cbd5e1",
    relief="flat",
    padx=10,
)
btn_limpiar.pack(side="left", padx=5)

btn_micro = tk.Button(
    frame_input,
    text="🎤",
    command=None,
    bg="#111827",
    fg="#cbd5e1",
    relief="flat",
    padx=10,
)
btn_micro.pack(side="left", padx=5)


def resolver_ruta_alias(texto):
    t = texto.lower()
    usuario_texto = _detectar_usuario_desde_texto(texto)
    if usuario_texto:
        _guardar_known_path("windows_user", usuario_texto)
    home = _descubrir_home_windows(usuario_texto)
    escritorio_descubierto = descubrir_ruta_escritorio()
    # Tolera errores comunes de escritura: "escriitorio", "escritiro", etc.
    if "desktop" in t or "escritorio" in t or "escri" in t:
        return escritorio_descubierto
    if "documentos" in t or "documents" in t:
        return home / "Documents"
    if "descargas" in t or "downloads" in t:
        return home / "Downloads"
    if "imagenes" in t or "pictures" in t:
        return home / "Pictures"
    if "musica" in t or "music" in t:
        return home / "Music"
    if "videos" in t:
        return home / "Videos"
    if "temp" in t or "temporal" in t:
        return Path(os.environ.get("TEMP", str(home / "AppData" / "Local" / "Temp")))
    if "esa carpeta" in t or "esa ruta" in t or "ahi" in t or "ahí" in t:
        known = CONFIG.get("known_paths", {}) if isinstance(CONFIG.get("known_paths"), dict) else {}
        last_folder = str(known.get("last_folder", "")).strip()
        if last_folder:
            p = Path(last_folder)
            if p.exists() and p.is_dir():
                return p
    return None


def _obtener_ultima_carpeta_contexto():
    known = CONFIG.get("known_paths", {}) if isinstance(CONFIG.get("known_paths"), dict) else {}
    last_folder = str(known.get("last_folder", "")).strip()
    if not last_folder:
        return None
    p = Path(last_folder)
    if p.exists() and p.is_dir():
        return p
    return None


def _normalizar_nombre_carpeta_referencia(valor):
    s = (valor or "").strip()
    if not s:
        return ""
    m_nombre = re.search(r"(?i)\bcon\s+(?:el\s+)?nombre\s+([a-zA-Z0-9_\-\s]{1,80})$", s)
    if m_nombre:
        s = m_nombre.group(1).strip()
    s = re.sub(r"(?i)^\s*(la|el|una|un)\s+", "", s).strip()
    s = re.sub(r"(?i)\bcarpeta\b", "", s).strip()
    s = re.sub(r"(?i)\bllamad[ao]s?\b", "", s).strip()
    s = re.sub(r"(?i)\bde nombre\b", "", s).strip()
    s = re.sub(r"(?i)\ben\s+mi\s+escritorio\b", "", s).strip()
    s = re.sub(r"(?i)\ben\s+el\s+escritorio\b", "", s).strip()
    s = re.sub(r"(?i)\ben\s+escritorio\b", "", s).strip()
    s = re.sub(r"(?i)\bmi\s+escritorio\b", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip(" .,:;")
    return s


def acciones_locales_desde_texto(texto):
    t = texto.lower().strip()
    ruta_alias = resolver_ruta_alias(t)
    usuario_texto = _detectar_usuario_desde_texto(texto)
    if usuario_texto:
        _guardar_known_path("windows_user", usuario_texto)

    if any(k in t for k in ("listar", "lista", "mostrar")) and any(
        k in t for k in ("archivos", "elementos", "carpeta", "directorio")
    ):
        destino_lista = ruta_alias or Path.home()
        if any(k in t for k in ("esa carpeta", "esa ruta", "ahí", "ahi")):
            destino_ctx = resolver_ruta_alias(t)
            if destino_ctx is not None:
                destino_lista = destino_ctx
        return [{"accion": "listar_directorio", "args": {"path": str(destino_lista)}}]

    if usuario_texto and any(k in t for k in ("escritorio", "desktop", "buscar", "encuentra")):
        return [{"accion": "listar_directorio", "args": {"path": str(descubrir_ruta_escritorio())}}]

    if ("abre " in t or "abrir " in t) and ("http://" in t or "https://" in t):
        m = re.search(r"(https?://\S+)", texto)
        if m:
            return [{"accion": "abrir_url", "args": {"url": m.group(1)}}]

    if any(k in t for k in ("llama api", "consulta api", "api get", "api post")):
        m = re.search(r"(https?://\S+)", texto)
        if m:
            metodo = "GET"
            if "post" in t:
                metodo = "POST"
            elif "put" in t:
                metodo = "PUT"
            elif "delete" in t:
                metodo = "DELETE"
            return [{"accion": "llamar_api", "args": {"url": m.group(1), "metodo": metodo}}]

    if any(k in t for k in ("dispara webhook", "lanzar webhook", "ejecuta webhook")):
        return [{"accion": "disparar_webhook", "args": {"nombre_evento": texto.strip()[:120], "payload": {}}}]

    if any(k in t for k in ("orquesta", "orquestador", "workflow")) and any(
        k in t for k in ("lanzar", "ejecuta", "dispara", "corre")
    ):
        return [
            {
                "accion": "llamar_orquestador",
                "args": {"accion": "run_workflow", "payload": {"prompt": texto.strip()}},
            }
        ]

    if ("buscar" in t or "encuentra" in t) and ("*.") in t:
        m = re.search(r"(\*\.[a-zA-Z0-9]+)", t)
        patron = m.group(1) if m else "*"
        return [{"accion": "buscar_archivos", "args": {"path": str(ruta_alias or Path.home()), "patron": patron}}]

    if (
        "carpeta" in t
        and any(k in t for k in ("crear", "crea", "nueva", "haz", "genera"))
        and not any(k in t for k in ("archivo", "archivos", "documento", "documentos"))
    ):
        m = re.search(r"['\"]([^'\"]+)['\"]", texto)
        if m:
            return [{"accion": "crear_carpeta", "args": {"path": m.group(1)}}]
        # Soporte de lenguaje natural sin comillas:
        # "crea una carpeta death en mi escritorio"
        # "en mi escritorio crea la carpeta death"
        nombre = None
        m1 = re.search(
            r"(?i)(?:crear|crea|nueva)\s+(?:una\s+)?carpeta\s+([a-zA-Z0-9_\-\s]{1,80}?)(?:\s+en\b|$)",
            texto.strip(),
        )
        if m1:
            nombre = m1.group(1).strip(" .,:;")
        if not nombre:
            m2 = re.search(
                r"(?i)carpeta\s+([a-zA-Z0-9_\-\s]{1,80}?)(?:\s+en\b|$)",
                texto.strip(),
            )
            if m2:
                nombre = m2.group(1).strip(" .,:;")
        nombre = _normalizar_nombre_carpeta_referencia(nombre)
        if nombre:
            base = ruta_alias or descubrir_ruta_escritorio()
            return [{"accion": "crear_carpeta", "args": {"path": str(base / nombre)}}]

    if (
        ("crear" in t or "crea" in t or "genera" in t)
        and ("archivo" in t or "documento" in t)
        and ("aleatorio" not in t and "aleatorios" not in t)
        and ("archivos" not in t)
        and (re.search(r"\b\d{1,4}\b", t) is None)
    ):
        m_nombre = re.search(r"['\"]([^'\"]+\.[a-zA-Z0-9]{2,5})['\"]", texto)
        m_contenido = re.search(r"(?:contenido|texto)\s*:\s*(.+)$", texto, flags=re.IGNORECASE)
        contenido = (m_contenido.group(1).strip() if m_contenido else "Generado por agente local.")
        tipo = "txt"
        if "word" in t or ".docx" in t:
            tipo = "docx"
        elif "excel" in t or ".xlsx" in t:
            tipo = "xlsx"
        elif "powerpoint" in t or "ppt" in t or ".pptx" in t:
            tipo = "pptx"
        elif ".txt" in t:
            tipo = "txt"
        if m_nombre:
            destino = normalizar_ruta(m_nombre.group(1))
        else:
            destino = (ruta_alias or descubrir_ruta_escritorio()) / f"nuevo_documento.{tipo}"
        return [
            {
                "accion": "crear_archivo_especifico",
                "args": {"path": str(destino), "tipo": tipo, "contenido": contenido},
            }
        ]

    if any(k in t for k in ("edita", "editar", "agrega al archivo", "escribe en archivo")):
        m_arch = re.search(r"['\"]([^'\"]+\.[a-zA-Z0-9]{2,5})['\"]", texto)
        m_contenido = re.search(r"(?:contenido|texto)\s*:\s*(.+)$", texto, flags=re.IGNORECASE)
        if m_arch and m_contenido:
            return [
                {
                    "accion": "editar_archivo",
                    "args": {
                        "path": m_arch.group(1),
                        "contenido": m_contenido.group(1).strip(),
                        "reemplazar": ("reemplaza" in t or "sobrescribe" in t),
                    },
                }
            ]

    if ("crear" in t or "crea" in t or "genera" in t) and ("archivo" in t or "archivos" in t or "elemento" in t or "elementos" in t):
        m_cant = re.search(r"\b(\d{1,4})\b", t)
        if m_cant is None and ("aleatorio" not in t and "aleatorios" not in t):
            # Si no pide cantidad ni aleatoriedad, no forzamos lote.
            return []
        cantidad = int(m_cant.group(1)) if m_cant else 10
        m_carpeta = re.search(
            r"(?i)(?:en|dentro de)\s+([a-zA-Z0-9_\\/:.\-\s]+?)(?:\s+(?:crea|crear|genera)\b|$)",
            texto.strip(),
        )
        destino = None
        if m_carpeta:
            ruta_txt = m_carpeta.group(1).strip()
            # "en esa carpeta" debe apuntar al último folder creado/abierto.
            if any(k in ruta_txt.lower() for k in ("esa carpeta", "esa ruta", "ahí", "ahi")):
                destino = resolver_ruta_alias(ruta_txt)
            elif "carpeta " in ruta_txt.lower():
                m_nom = re.search(r"(?i)carpeta\s+([a-zA-Z0-9_\-\s]{1,80})", ruta_txt)
                if m_nom:
                    nombre = _normalizar_nombre_carpeta_referencia(m_nom.group(1))
                    base = resolver_ruta_alias(texto) or descubrir_ruta_escritorio()
                    candidato = base / nombre
                    destino = candidato if candidato.exists() else candidato
            elif re.match(r"^[a-zA-Z0-9_\-\s]{1,80}$", ruta_txt) and ("\\" not in ruta_txt and "/" not in ruta_txt and ":" not in ruta_txt):
                nombre_ref = _normalizar_nombre_carpeta_referencia(ruta_txt)
                base = resolver_ruta_alias(texto) or descubrir_ruta_escritorio()
                destino = base / (nombre_ref or ruta_txt.strip())
            else:
                destino = normalizar_ruta(ruta_txt)
        if destino is None:
            ctx = _obtener_ultima_carpeta_contexto()
            if ctx is not None:
                destino = ctx
        if destino is None:
            destino = ruta_alias or descubrir_ruta_escritorio()
        return [
            {
                "accion": "crear_archivos_aleatorios",
                "args": {"path": str(destino), "cantidad": cantidad, "prefijo": "item", "extension": ".txt"},
            }
        ]

    if any(k in t for k in ("elimina todo", "borra todo", "vaciar", "limpia todo")) and (
        "temp" in t or "temporal" in t
    ):
        ruta_temp = Path(os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp")))
        return [{"accion": "vaciar_carpeta", "args": {"path": str(ruta_temp)}}]

    if "organiza" in t and "carpeta" in t and (
        "deja afuera el ejecutable" in t
        or "sin ejecutable" in t
        or "sin exe" in t
        or ".exe" in t
    ):
        ruta = resolver_ruta_alias(t) or Path.home() / "Desktop"
        return [
            {
                "accion": "organizar_carpeta",
                "args": {"path": str(ruta), "excluir_ext": ".exe"},
            }
        ]

    if any(k in t for k in ("voz", "tono", "habla")) and any(
        k in t for k in ("mas", "más", "pon", "cambia", "quiero")
    ):
        args = {}
        if any(k in t for k in ("femenina", "mujer")):
            args["genero"] = "femenina"
        elif any(k in t for k in ("masculina", "hombre")):
            args["genero"] = "masculina"

        if any(k in t for k in ("suave", "calida", "cálida")):
            args["estilo"] = "suave"
        elif any(k in t for k in ("grave", "profunda")):
            args["estilo"] = "grave"

        if "rápida" in t or "rapida" in t:
            args["velocidad"] = 220
        elif "lenta" in t:
            args["velocidad"] = 150

        m_vol = re.search(r"(volumen|volume)\s*(\d{1,3})", t)
        if m_vol:
            args["volumen"] = int(m_vol.group(2))

        if args:
            return [{"accion": "configurar_voz", "args": args}]

    if any(k in t for k in ("microfono", "micrófono", "mic", "escucha")) and any(
        k in t for k in ("encendido", "activar", "prender", "on")
    ):
        CONFIG["voz_entrada_activa"] = True
        guardar_config(CONFIG)
        return []

    if any(k in t for k in ("microfono", "micrófono", "mic", "escucha")) and any(
        k in t for k in ("apagar", "off", "desactivar")
    ):
        CONFIG["voz_entrada_activa"] = False
        guardar_config(CONFIG)
        return []

    return []


def es_intencion_operativa(texto):
    t = (texto or "").lower()
    claves = (
        "archivo",
        "archivos",
        "carpeta",
        "directorio",
        "escritorio",
        "desktop",
        "documento",
        "office",
        "word",
        "excel",
        "powerpoint",
        "descarga",
        "listar",
        "mostrar",
        "buscar",
        "abrir",
        "mover",
        "copiar",
        "eliminar",
        "borrar",
        "vaciar",
        "api",
        "webhook",
        "orquestador",
        "workflow",
        "comando",
        "cmd",
    )
    return any(k in t for k in claves)


def generar_acciones_con_modelo(texto):
    memoria = obtener_contexto_db(limite=20)
    historial = construir_resumen_contexto(memoria, max_chars=1400)
    prompt_plan = f"""
Convierte la solicitud del usuario a acciones ejecutables.
Nunca respondas que no puedes. Siempre intenta una estrategia.
Devuelve JSON válido con:
{{
  "respuesta": "resumen corto",
  "acciones": [
    {{"accion": "nombre", "args": {{}}}}
  ]
}}

Acciones permitidas:
- obtener_hora
- obtener_ip_local
- obtener_ip_publica
- listar_directorio(path)
- leer_archivo(path)
- escribir_archivo(path, contenido, append)
- crear_archivo_especifico(path, tipo, contenido)
- editar_archivo(path, contenido, reemplazar)
- crear_carpeta(path)
- eliminar_ruta(path)
- mover_ruta(origen, destino)
- copiar_ruta(origen, destino)
- buscar_archivos(path, patron)
- crear_archivos_aleatorios(path, cantidad, prefijo, extension)
- abrir_ruta(path)
- abrir_url(url)
- llamar_api(url, metodo, json_data, data_text, headers, timeout)
- disparar_webhook(nombre_evento, payload)
- llamar_orquestador(accion, payload)
- organizar_carpeta(path, excluir_ext)
- vaciar_carpeta(path)
- configurar_voz(estilo, velocidad, volumen, genero)
- ejecutar_cmd(cmd)

Historial:
{historial}

Solicitud:
{texto}
"""
    proveedor = CONFIG.get("proveedor_ia", "local")
    if proveedor == "online":
        contenido = chat_online_groq(
            "Eres un planner de acciones. Devuelves JSON válido estricto.",
            prompt_plan,
        )
    else:
        validar_ollama_disponible()
        res = ollama.chat(
            model=CONFIG["model"],
            messages=[{"role": "user", "content": prompt_plan}],
            keep_alive="30m",
        )
        contenido = res["message"]["content"]
    try:
        data = json.loads(contenido)
        acciones = data.get("acciones", [])
        if isinstance(acciones, list):
            return data.get("respuesta", ""), acciones
    except json.JSONDecodeError:
        pass
    return "", []


def _procesar_mensaje(texto):
    global ULTIMA_SOLICITUD_USUARIO
    chat_window.after(0, lambda: estado_var.set("Pensando..."))
    chat_window.after(0, expandir_si_compacto)
    texto_l = texto.lower()
    solicitud_original = texto.strip()
    tipo_tiempo = detectar_consulta_fecha_hora(solicitud_original)
    intencion = detectar_intencion_principal(solicitud_original)

    if not any(k in texto_l for k in ("aprende", "aprendelo", "apréndelo")):
        ULTIMA_SOLICITUD_USUARIO = solicitud_original

    if tipo_tiempo:
        cache_key = f"fecha_hora::{tipo_tiempo}::{normalizar_texto_cache(solicitud_original)}"
        ttl_seg = 10 if tipo_tiempo in ("hora", "fecha_hora") else 24 * 3600
        respuesta_cache = obtener_cache_respuesta(cache_key, max_age_seconds=ttl_seg)
        respuesta_final = respuesta_cache or formatear_fecha_hora_bonita(tipo_tiempo)
        if not respuesta_cache:
            guardar_cache_respuesta(cache_key, respuesta_final)
        def _post_tiempo():
            mensaje(respuesta_final, "ia")
            guardar_mensaje_db("assistant", respuesta_final)
            registrar_ejecucion(solicitud_original, respuesta_final)
            estado_var.set("Listo")
            if CONFIG.get("voz_activa", True):
                hablar_garantizado(respuesta_final)
        chat_window.after(0, _post_tiempo)
        return

    # Respuestas conversacionales locales para evitar bucles del planner.
    if intencion == "general":
        resp_local = respuesta_conversacional_local(solicitud_original)
        if resp_local:
            def _post_conv():
                mensaje(resp_local, "ia")
                guardar_mensaje_db("assistant", resp_local)
                registrar_ejecucion(solicitud_original, resp_local)
                estado_var.set("Listo")
                if CONFIG.get("voz_activa", True):
                    hablar_garantizado(resp_local)
            chat_window.after(0, _post_conv)
            return

    if any(k in texto_l for k in ("aprende a hacerlo", "aprende hacerlo", "aprendelo", "apréndelo")):
        if not ULTIMA_SOLICITUD_USUARIO:
            respuesta_final = "No tengo una solicitud previa para aprender. Dame primero una tarea concreta."
        else:
            acciones_aprendidas = acciones_locales_desde_texto(ULTIMA_SOLICITUD_USUARIO)
            if not acciones_aprendidas:
                respuesta_final = (
                    "Intenté aprender, pero no encontré un patrón automático para esa tarea todavía. "
                    "Dímela con más detalle (ruta, archivo o patrón)."
                )
            else:
                guardar_habilidad(ULTIMA_SOLICITUD_USUARIO, acciones_aprendidas)
                resultados_aprendidos = ejecutar_acciones(acciones_aprendidas)
                respuesta_final = (
                    "Aprendido y ejecutado en tiempo real.\n\n" + "\n".join(resultados_aprendidos)
                )

    # Ejecuta atajos locales solo en intenciones operativas explícitas.
    acciones_locales = acciones_locales_desde_texto(texto) if es_intencion_operativa(solicitud_original) else []
    if acciones_locales:
        acciones_locales = filtrar_acciones_por_intencion(acciones_locales, solicitud_original)
    if acciones_locales:
        resultados_locales = ejecutar_acciones(acciones_locales)
        exito_local = not any(
            "error" in (r or "").lower() or "no pudo" in (r or "").lower() or "no se pudo" in (r or "").lower()
            for r in resultados_locales
        )
        registrar_leccion(solicitud_original, acciones_locales, "\n".join(resultados_locales), exito_local)
        if exito_local:
            guardar_habilidad(solicitud_original, acciones_locales)
        respuesta_final = "\n".join(resultados_locales)

        def _post_local():
            mensaje(respuesta_final, "ia")
            guardar_mensaje_db("assistant", respuesta_final)
            registrar_ejecucion(solicitud_original, respuesta_final)
            estado_var.set("Listo")
            if CONFIG.get("voz_activa", True):
                hablar_garantizado(respuesta_final)

        chat_window.after(0, _post_local)
        return

    if es_intencion_operativa(solicitud_original):
        propuesta_auto = []
        t_auto = solicitud_original.lower()
        base_auto = resolver_ruta_alias(solicitud_original) or descubrir_ruta_escritorio()
        if ("crear" in t_auto or "genera" in t_auto) and ("docx" in t_auto or "word" in t_auto):
            propuesta_auto = [{"accion": "crear_archivo_especifico", "args": {"path": str(base_auto / "documento_generado.docx"), "tipo": "docx", "contenido": "Documento generado automáticamente"}}]
        elif ("crear" in t_auto or "genera" in t_auto) and ("xlsx" in t_auto or "excel" in t_auto):
            propuesta_auto = [{"accion": "crear_archivo_especifico", "args": {"path": str(base_auto / "tabla_generada.xlsx"), "tipo": "xlsx", "contenido": "columna1,columna2\nvalor1,valor2"}}]
        elif ("crear" in t_auto or "genera" in t_auto) and ("pptx" in t_auto or "powerpoint" in t_auto):
            propuesta_auto = [{"accion": "crear_archivo_especifico", "args": {"path": str(base_auto / "presentacion_generada.pptx"), "tipo": "pptx", "contenido": "Presentación generada automáticamente"}}]

        if propuesta_auto:
            resultados_auto = ejecutar_acciones(propuesta_auto)
            exito_auto = not any("error" in (r or "").lower() or "no pude" in (r or "").lower() for r in resultados_auto)
            registrar_leccion(solicitud_original, propuesta_auto, "\n".join(resultados_auto), exito_auto)
            if exito_auto:
                guardar_habilidad(solicitud_original, propuesta_auto)
            respuesta_auto = "\n".join(resultados_auto)

            def _post_auto():
                mensaje(respuesta_auto, "ia")
                guardar_mensaje_db("assistant", respuesta_auto)
                registrar_ejecucion(solicitud_original, respuesta_auto)
                estado_var.set("Listo")
                if CONFIG.get("voz_activa", True):
                    hablar_garantizado(respuesta_auto)
            chat_window.after(0, _post_auto)
            return

    try:
        respuesta_cruda = decidir(texto)
    except Exception as ex:
        respuesta_cruda = json.dumps(
            {
                "respuesta": f"Error consultando proveedor IA: {ex}",
                "acciones": [],
            },
            ensure_ascii=False,
        )

    try:
        data = json.loads(respuesta_cruda)
    except json.JSONDecodeError as ex:
        texto_plano = (respuesta_cruda or "").strip()
        if texto_plano:
            respuesta = texto_plano
        else:
            respuesta = "No devolviste JSON útil."
        data = {"respuesta": respuesta, "acciones": []}

    respuesta = data.get("respuesta", "").strip()
    acciones = data.get("acciones", [])
    acciones = filtrar_acciones_por_intencion(acciones, solicitud_original)
    if not acciones and any(
        s in respuesta.lower()
        for s in (
            "no puedo acceder",
            "no tengo la capacidad",
            "no puedo acceder directamente",
        )
    ):
        ruta_alias = resolver_ruta_alias(texto_l)
        if ruta_alias:
            acciones = [{"accion": "listar_directorio", "args": {"path": str(ruta_alias)}}]
            if not respuesta:
                respuesta = "Lo consulto localmente ahora mismo."

    # Solo activar planner cuando la solicitud parezca operativa.
    # Evita arrastrar acciones del turno previo en preguntas conversacionales.
    requiere_plan = es_intencion_operativa(solicitud_original)
    if requiere_plan and ((not acciones) or any(p in respuesta.lower() for p in NEGATIVE_PATTERNS)):
        try:
            r_plan, a_plan = generar_acciones_con_modelo(texto)
            if a_plan:
                acciones = a_plan
                if r_plan:
                    respuesta = r_plan
            else:
                # Si no hay plan válido, responde sin ejecutar comandos arbitrarios.
                acciones = []
                if not respuesta:
                    respuesta = (
                        "No encontré un plan ejecutable para esa petición aún. "
                        "Si quieres, te muestro opciones o me das una instrucción más concreta."
                    )
        except Exception as ex:
            acciones = []
            if not respuesta:
                respuesta = f"Error en planificación: {ex}"

    if not tipo_tiempo and acciones:
        acciones = [a for a in acciones if a.get("accion") != "obtener_hora"]

    if intencion in ("archivos", "api", "voz", "general") and acciones:
        acciones = [a for a in acciones if a.get("accion") not in ("obtener_ip_local", "obtener_ip_publica")]

    resultados = ejecutar_acciones(acciones)

    def _resultado_es_fallido(linea):
        s = (linea or "").lower()
        patrones = (
            "error",
            "no pude",
            "no tengo",
            "ruta no encontrada",
            "url inválida",
            "api.example.com",
            "failed",
            "max retries exceeded",
        )
        return any(p in s for p in patrones)

    hubo_error = any(_resultado_es_fallido(r) for r in resultados)

    # Aprende automáticamente lo que funcionó.
    if acciones and not hubo_error:
        guardar_habilidad(solicitud_original, acciones)
    registrar_leccion(solicitud_original, acciones, "\n".join(resultados), not hubo_error)

    respuesta_final = respuesta or "Sin respuesta de texto."
    if resultados:
        respuesta_final += "\n\n" + "\n".join(resultados)

    def _post():
        mensaje(respuesta_final, "ia")
        guardar_mensaje_db("assistant", respuesta_final)
        registrar_ejecucion(solicitud_original, respuesta_final)
        estado_var.set("Listo")
        if CONFIG.get("voz_activa", True):
            hablar_garantizado(respuesta_final)

    chat_window.after(0, _post)


def enviar(event=None):
    expandir_si_compacto()
    texto = entrada.get().strip()
    entrada.delete(0, tk.END)
    if not texto:
        return

    mensaje(texto, "user")
    guardar_mensaje_db("user", texto)
    estado_var.set("Pensando...")
    def _worker():
        try:
            _procesar_mensaje(texto)
        except Exception as ex:
            err = f"Error interno procesando mensaje: {ex}"
            def _post_err():
                mensaje(err, "ia")
                guardar_mensaje_db("assistant", err)
                registrar_ejecucion(texto, err)
                estado_var.set("Listo")
            chat_window.after(0, _post_err)
    threading.Thread(target=_worker, daemon=True).start()


def escuchar_y_enviar():
    global escucha_activa
    if sr is None:
        estado_var.set("Falta SpeechRecognition en este entorno.")
        chat_window.after(2200, lambda: estado_var.set("Listo"))
        return
    if escucha_activa:
        return
    if not CONFIG.get("voz_entrada_activa", True):
        estado_var.set("Micrófono desactivado")
        chat_window.after(1500, lambda: estado_var.set("Listo"))
        return

    escucha_activa = True
    estado_var.set("Escuchando...")
    btn_micro.configure(text="Escuchando...", state="disabled")

    def _run():
        global escucha_activa
        r = sr.Recognizer()
        texto = ""
        error = None
        try:
            mic_nombre = mic_var.get().strip()
            mic_index = None
            nombres = listar_microfonos()
            if mic_nombre and mic_nombre in nombres:
                mic_index = nombres.index(mic_nombre)
            if mic_nombre and mic_index is None:
                raise RuntimeError(f"Micrófono no encontrado: {mic_nombre}")

            with sr.Microphone(device_index=mic_index) as source:
                r.adjust_for_ambient_noise(source, duration=0.5)
                audio = r.listen(source, timeout=8, phrase_time_limit=14)
            idioma = CONFIG.get("voz_entrada_idioma", "es-ES")
            texto = r.recognize_google(audio, language=idioma)
        except sr.WaitTimeoutError:
            error = "No detecté voz a tiempo."
        except sr.UnknownValueError:
            error = "No pude entender lo que dijiste."
        except sr.RequestError as ex:
            error = f"Error de reconocimiento: {ex}"
        except Exception as ex:
            error = f"Error con micrófono: {ex}"

        def _post():
            global escucha_activa
            escucha_activa = False
            btn_micro.configure(text="🎤", state="normal")
            if error:
                estado_var.set(error)
                chat_window.after(2200, lambda: estado_var.set("Listo"))
                return

            entrada.delete(0, tk.END)
            entrada.insert(0, texto)
            estado_var.set(f"Texto reconocido ({mic_var.get() or 'predeterminado'})")
            chat_window.after(1200, lambda: estado_var.set("Listo"))
            enviar()

        chat_window.after(0, _post)

    threading.Thread(target=_run, daemon=True).start()


btn_micro.configure(command=escuchar_y_enviar)

def refrescar_microfonos():
    nombres = listar_microfonos()
    combo_mic.configure(values=nombres, state="readonly" if nombres else "disabled")
    if nombres and mic_var.get() not in nombres:
        mic_var.set(nombres[0])
    CONFIG["voz_entrada_microfono"] = mic_var.get()
    guardar_config(CONFIG)
    estado_var.set("Micrófonos actualizados")
    chat_window.after(1200, lambda: estado_var.set("Listo"))

btn_refresh_mic = tk.Button(
    frame_input,
    text="Mic",
    command=refrescar_microfonos,
    bg="#111827",
    fg="#cbd5e1",
    relief="flat",
    padx=10,
)
btn_refresh_mic.pack(side="left", padx=5)

def on_mic_change(_event=None):
    CONFIG["voz_entrada_microfono"] = mic_var.get()
    guardar_config(CONFIG)

combo_mic.bind("<<ComboboxSelected>>", on_mic_change)


btn_enviar = tk.Button(
    frame_input,
    text="Enviar",
    command=enviar,
    bg="#1d4ed8",
    fg="#f8fafc",
    relief="flat",
    padx=14,
)
btn_enviar.pack(side="right", padx=10)

entrada.bind("<Return>", enviar)
combo_voz_style.bind("<<ComboboxSelected>>", on_voice_ui_change)
combo_voz_speed.bind("<<ComboboxSelected>>", on_voice_ui_change)
combo_provider.bind("<<ComboboxSelected>>", on_provider_change)
combo_online_model.bind("<<ComboboxSelected>>", on_provider_change)
chat_window.bind("<FocusIn>", lambda e: expandir_si_compacto())
chat_window.bind("<Configure>", lambda e: guardar_geometria_actual())
chat_window.protocol("WM_DELETE_WINDOW", cerrar_app)

def depurar_datos_locales():
    return


def limpiar_habilidades_y_automatizaciones_conflictivas():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT id, trigger_texto, acciones_json FROM habilidades ORDER BY id ASC").fetchall()
        to_delete = []
        for hid, trigger_texto, acciones_json in rows:
            t = normalizar_texto_cache(trigger_texto or "")
            if not t:
                continue
            try:
                acciones = json.loads(acciones_json or "[]")
            except Exception:
                to_delete.append(hid)
                continue
            if not isinstance(acciones, list) or not acciones:
                to_delete.append(hid)
                continue

            # Reglas de saneamiento para evitar recuerdos tóxicos.
            tiene_archivos = ("archivo" in t or "archivos" in t)
            tiene_carpeta = ("carpeta" in t)
            primer_accion = str(acciones[0].get("accion", "")).strip() if isinstance(acciones[0], dict) else ""

            # Si el trigger pide archivos, no debe aprender acciones de abrir_ruta/crear_carpeta.
            if tiene_archivos and primer_accion in ("abrir_ruta", "crear_carpeta"):
                to_delete.append(hid)
                continue
            # Si dice "carpeta llamada X", no debe guardar literal "llamada x" en el path.
            if tiene_carpeta and "llamada " in t:
                path_bad = False
                for a in acciones:
                    if isinstance(a, dict):
                        p = str((a.get("args") or {}).get("path", "")).lower()
                        if "\\llamada " in p or "/llamada " in p:
                            path_bad = True
                            break
                if path_bad:
                    to_delete.append(hid)
                    continue

        if to_delete:
            q = ",".join("?" for _ in to_delete)
            conn.execute(f"DELETE FROM habilidades WHERE id IN ({q})", tuple(to_delete))
            conn.commit()


init_db()
asegurar_habilidades_base()
depurar_datos_locales()
limpiar_habilidades_y_automatizaciones_conflictivas()
mensaje("Asistente listo. Ventana minimalista activa.", "system")
programar_auto_compacto()

if __name__ == "__main__":
    root.mainloop()


