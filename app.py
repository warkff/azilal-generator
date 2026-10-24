"""HTTP API for the Azilal report generator.

POST /generate  JSON: {member, type: program|stats|both, month, year,
                       lessons: [{title, day, mosque?, attendance?, type?}]}
Returns one .xlsx (single kind) or a .zip (both kinds).
GET  /health    -> {"ok": true}
Stdlib only.
"""
import io
import json
import os
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlparse, parse_qs

import pipeline
import whatsapp

XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _disposition(filename):
    return f"attachment; filename*=UTF-8''{quote(filename)}"


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/whatsapp":
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            code, body = whatsapp.verify(params)
            body = body.encode()
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/health":
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self._cors()
            self.end_headers()

    def do_POST(self):
        if urlparse(self.path).path == "/whatsapp":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                payload = {}
            threading.Thread(target=whatsapp.process_payload,
                             args=(payload,), daemon=True).start()
            self.send_response(200)
            self._cors()
            self.end_headers()
            return
        if self.path != "/generate":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            files = pipeline.generate(req)
        except Exception as e:  # report cleanly to the caller
            body = json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
            self.send_response(400)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if len(files) == 1:
            name, blob = next(iter(files.items()))
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", XLSX_CT)
            self.send_header("Content-Disposition", _disposition(name))
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
        else:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for name, blob in files.items():
                    z.writestr(name, blob)
            blob = buf.getvalue()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", _disposition("تقارير.zip"))
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
