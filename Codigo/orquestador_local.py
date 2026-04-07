import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from texto import AgentCore, CONFIG, proveedor_ui_value

HOST = os.environ.get("ORQUESTADOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("ORQUESTADOR_PORT", "8765"))
core = AgentCore()


def _provider_actual():
    return proveedor_ui_value(CONFIG.get("proveedor_ia", "local"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"ok": False, "error": "JSON invalido"})
            return None

    def _health_payload(self):
        return {
            "ok": True,
            "service": "orquestador_local",
            "provider_mode": _provider_actual(),
        }

    def _require_token(self):
        token = (CONFIG.get("orquestador_token") or "").strip()
        if not token:
            return True
        recibido = (self.headers.get("X-Orquestador-Token") or "").strip()
        if recibido != token:
            self._send(401, {"ok": False, "error": "Token invalido"})
            return False
        return True

    def _run_prompt(self, prompt):
        prompt = str(prompt or "").strip()
        if not prompt:
            self._send(400, {"ok": False, "error": "prompt vacio"})
            return
        answer = core.process_prompt(prompt)
        self._send(200, {"ok": True, "answer": answer, "provider_mode": _provider_actual()})

    def do_GET(self):
        if self.path == "/health":
            self._send(200, self._health_payload())
            return
        self._send(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        if not self._require_token():
            return
        body = self._read_json_body()
        if body is None:
            return

        if self.path == "/ask":
            self._run_prompt(body.get("prompt", ""))
            return

        if self.path == "/accion":
            accion = str(body.get("accion", "")).strip().lower()
            payload = body.get("payload") or {}
            if accion == "health":
                self._send(200, self._health_payload())
                return
            if accion in ("ask", "run_workflow"):
                prompt = body.get("prompt") or payload.get("prompt") or payload.get("texto") or ""
                self._run_prompt(prompt)
                return
            self._send(400, {"ok": False, "error": f"Accion no soportada: {accion}"})
            return

        self._send(404, {"ok": False, "error": "Not found"})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Orquestador escuchando en http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
