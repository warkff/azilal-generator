"""WhatsApp Cloud API adapter: webhook receive, send text/documents,
voice transcription (OpenAI Whisper) and image OCR (Google Vision).
All credentials come from environment variables:
  WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_VERIFY_TOKEN,
  OPENAI_API_KEY (voice), GOOGLE_VISION_API_KEY (image OCR).
Stdlib only.
"""
import base64
import json
import os
import urllib.request
import urllib.error

import bot

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")


def _req(method, url, data=None, headers=None, timeout=120):
    r = urllib.request.Request(url, data=data, method=method,
                               headers=headers or {})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.read()


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def send_text(to, body):
    data = json.dumps({"messaging_product": "whatsapp", "to": to,
                       "type": "text", "text": {"body": body}}).encode()
    _req("POST", f"{GRAPH}/{PHONE_ID}/messages", data,
         {**_auth(), "Content-Type": "application/json"})


def send_document(to, filename, blob):
    boundary = "----azilalboundary"
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"messaging_product\"\r\n\r\nwhatsapp\r\n")
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\napplication/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n")
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                 "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n")
    payload = "".join(parts).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    resp = _req("POST", f"{GRAPH}/{PHONE_ID}/media", payload,
                {**_auth(), "Content-Type": f"multipart/form-data; boundary={boundary}"})
    media_id = json.loads(resp)["id"]
    data = json.dumps({"messaging_product": "whatsapp", "to": to,
                       "type": "document",
                       "document": {"id": media_id, "filename": filename}}).encode()
    _req("POST", f"{GRAPH}/{PHONE_ID}/messages", data,
         {**_auth(), "Content-Type": "application/json"})


def download_media(media_id):
    meta = json.loads(_req("GET", f"{GRAPH}/{media_id}", headers=_auth()))
    return _req("GET", meta["url"], headers=_auth())


def transcribe(audio_bytes):
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("خدمة الصوت غير مهيأة (OPENAI_API_KEY مفقود)")
    boundary = "----azilalwhisper"
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n")
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nar\r\n")
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"voice.ogg\"\r\n"
                 "Content-Type: audio/ogg\r\n\r\n")
    payload = "".join(parts).encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()
    resp = _req("POST", "https://api.openai.com/v1/audio/transcriptions", payload,
                {"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(resp)["text"]


def ocr(image_bytes):
    key = os.environ.get("GOOGLE_VISION_API_KEY", "")
    if not key:
        raise RuntimeError("خدمة الصور غير مهيأة (GOOGLE_VISION_API_KEY مفقود)")
    body = {"requests": [{
        "image": {"content": base64.b64encode(image_bytes).decode()},
        "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
        "imageContext": {"languageHints": ["ar"]}}]}
    resp = _req("POST", f"https://vision.googleapis.com/v1/images:annotate?key={key}",
                json.dumps(body).encode(), {"Content-Type": "application/json"})
    data = json.loads(resp)
    return data["responses"][0].get("fullTextAnnotation", {}).get("text", "")


def verify(params):
    if (params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == VERIFY_TOKEN):
        return 200, params.get("hub.challenge", "")
    return 403, "verification failed"


def _execute(sender, actions):
    for act in actions:
        if act["type"] == "text":
            send_text(sender, act["body"])
        elif act["type"] == "files":
            for name, blob in act["files"].items():
                send_document(sender, name, blob)


def process_payload(payload):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                sender = msg.get("from")
                if not sender:
                    continue
                mtype = msg.get("type")
                try:
                    if mtype == "text":
                        actions = bot.handle(sender, msg["text"]["body"])
                    elif mtype in ("audio", "voice"):
                        text = transcribe(download_media(msg[mtype]["id"]))
                        actions = bot.handle(sender, "", media_text=text)
                    elif mtype == "image":
                        text = ocr(download_media(msg["image"]["id"]))
                        actions = bot.handle(sender, msg["image"].get("caption", ""),
                                             media_text=text)
                    elif mtype == "document":
                        actions = [{"type": "text", "body": "وصلني ملف، ولكن كنحتاج الدروس كتابةً أو صورة أو صوتاً 🎤"}]
                    else:
                        continue
                except Exception as e:
                    actions = [{"type": "text", "body": f"وقع مشكل: {e}"}]
                _execute(sender, actions)
