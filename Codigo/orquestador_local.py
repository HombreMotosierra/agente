import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from texto import AgentCore

HOST = "127.0.0.1"
PORT = 18790
core = AgentCore()

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "orquestador_local"})
            return
        self._send(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        if self.path != "/ask":
            self._send(404, {"ok": False, "error": "Not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"ok": False, "error": "JSON invalido"})
            return
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            self._send(400, {"ok": False, "error": "prompt vacio"})
            return
        answer = core.process_prompt(prompt)
        self._send(200, {"ok": True, "answer": answer})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Orquestador escuchando en http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
