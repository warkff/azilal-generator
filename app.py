"""HTTP API for the Azilal report generator.

POST /generate  JSON: {member, type: program|stats|both, month, year,
                       lessons: [{title, day, mosque?, attendance?, type?}]}
Returns one .xlsx (single kind) or a .zip (both kinds).
GET  /health    -> {"ok": true}
POST /gateway   JSON: {from, text, media_text} -> {"actions": [...]}
                 (WhatsApp gateway: Baileys forwards chats here, actions are
                 sent back through Baileys; files are base64-encoded)
GET  /qr        QR page for linking the user's own WhatsApp (gateway)
GET  /qr.png    the current QR image (gateway)
Stdlib only.
"""
import base64
import io
import json
import os
import shutil
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlparse, parse_qs

import bot
import pipeline
import whatsapp

XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_PNG = os.environ.get("QR_PNG", os.path.join(BASE_DIR, "qr.png"))
STATUS_JSON = os.environ.get("STATUS_JSON", os.path.join(BASE_DIR, "gateway-status.json"))


def _disposition(filename):
    return f"attachment; filename*=UTF-8''{quote(filename)}"


def _start_gateway(port):
    """Start the Baileys WhatsApp gateway (if its files exist), in background.

    The gateway links the user's own WhatsApp via QR (like WhatsApp Web)
    and forwards messages to POST /gateway on this same service.
    Runs `npm install` once if node_modules is missing, in a thread so
    startup stays fast. Kills any stale gateway process first (start.sh
    restarts app.py but not node). Never blocks the API.
    """
    gw_dir = os.path.join(BASE_DIR, "gateway")
    gw_js = os.path.join(gw_dir, "index.js")
    if not os.path.exists(gw_js):
        return
    if shutil.which("node") is None:
        print("gateway: node not found, skipping (install nodejs to enable WhatsApp gateway)", flush=True)
        return

    def _run():
        try:
            subprocess.run(["pkill", "-f", "node index.js"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not os.path.exists(os.path.join(gw_dir, "node_modules")):
                with open("/tmp/azilal-gateway-npm.log", "ab") as nlf:
                    subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                                   cwd=gw_dir, stdout=nlf, stderr=subprocess.STDOUT,
                                   timeout=300)
            env = dict(os.environ)
            env["BOT_URL"] = f"http://127.0.0.1:{port}/gateway"
            env["QR_PNG"] = QR_PNG
            env["STATUS_JSON"] = STATUS_JSON
            env["AUTH_DIR"] = os.path.join(gw_dir, "auth")
            with open("/tmp/azilal-gateway.log", "ab") as lf:
                subprocess.Popen(["node", "index.js"], cwd=gw_dir, env=env,
                                 stdout=lf, stderr=subprocess.STDOUT,
                                 start_new_session=True)
            print("gateway: started", flush=True)
        except Exception as e:
            print(f"gateway: failed to start: {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()


def _gateway_status():
    try:
        with open(STATUS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _send(self, code, body, ctype):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        if parsed.path == "/qr.png":
            if os.path.exists(QR_PNG):
                with open(QR_PNG, "rb") as f:
                    blob = f.read()
                self._send(200, blob, "image/png")
            else:
                self.send_response(404)
                self._cors()
                self.end_headers()
            return
        if parsed.path == "/qr":
            status = _gateway_status()
            state = status.get("state", "")
            if os.path.exists(QR_PNG):
                img = '<img src="/qr.png" style="width:320px;height:320px" alt="QR">'
                msg = "سكاني هاد QR من واتساب: الإعدادات ← الأجهزة المرتبطة ← ربط جهاز"
                refresh = '<meta http-equiv="refresh" content="15">'
            elif state == "connected":
                img = ""
                msg = "البوابة متصلة ✅ — البوت خدام على واتساب"
                refresh = ""
            elif state == "logged_out":
                img = ""
                msg = "الجلسة تسجلات الخروج من التيليفون — عاود شغّل المحرك باش يطلع QR جديد"
                refresh = '<meta http-equiv="refresh" content="15">'
            else:
                img = ""
                msg = "QR ما زال ما تولدش أو البوابة ما خداماش — عاود حمل الصفحة من بعد دقيقة"
                refresh = '<meta http-equiv="refresh" content="15">'
            html = (f"<!doctype html><html dir='rtl' lang='ar'><head><meta charset='utf-8'>"
                    f"{refresh}<title>ربط واتساب</title></head>"
                    f"<body style='font-family:sans-serif;text-align:center;padding:40px'>"
                    f"{img}<p style='font-size:18px'>{msg}</p></body></html>").encode("utf-8")
            self._send(200, html, "text/html; charset=utf-8")
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
        if urlparse(self.path).path == "/gateway":
            # Baileys gateway: synchronous — the gateway needs the actions
            # back to send them. Generation (LibreOffice) may take a while;
            # the gateway waits up to 5 minutes.
            length = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                req = {}
            try:
                actions = bot.handle(req.get("from", ""), req.get("text", ""),
                                     req.get("media_text"))
            except Exception as e:
                actions = [{"type": "text", "body": f"وقع مشكل: {e}"}]
            out = []
            for act in actions:
                if act["type"] == "files":
                    out.append({"type": "files",
                                "files": {name: base64.b64encode(blob).decode()
                                          for name, blob in act["files"].items()}})
                else:
                    out.append(act)
            body = json.dumps({"actions": out}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
            try:
                zip_name = pipeline.bundle_name(req)
            except Exception:
                zip_name = "تقارير.zip"
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", _disposition(zip_name))
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    _start_gateway(port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
