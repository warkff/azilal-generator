/* Azilal WhatsApp gateway (Baileys).
 *
 * Links the user's OWN WhatsApp number via QR code — the linked-device
 * model, exactly like WhatsApp Web. The number is NOT converted into a
 * bot number and keeps working normally in the WhatsApp app.
 *
 * Flow: incoming WhatsApp message -> transcribe voice notes via the
 * Groq transcribe Worker -> POST to the bot service (/gateway) ->
 * bot actions come back as JSON -> sent back through Baileys.
 *
 * WHO TALKS TO THE BOT: the user himself, in his own "Message yourself"
 * self-chat. The gateway answers ONLY that chat: auto-replying to his
 * contacts would spam his friends with bot menus. A self-chat message
 * sent from his phone arrives with fromMe=true, so the gateway tells
 * it apart from its own replies by the sent-message IDs it records.
 * Only 'notify' (new) messages are processed, never history sync, so a
 * reconnect can't replay old commands.
 *
 * Env:
 *   BOT_URL       bot service endpoint (default http://127.0.0.1:7860/gateway)
 *   QR_PNG        path of the QR image the bot service serves at /qr
 *   STATUS_JSON   path of the JSON status file served via /qr
 *   AUTH_DIR      Baileys session dir (KEEP OUT OF GIT — it grants access)
 *   TRANSCRIBE_URL Groq transcribe Worker (default azilal-transcribe)
 *
 * Started automatically by app.py on service start. No secrets live here:
 * transcription goes through the Worker, which holds the Groq key.
 */
const fs = require('fs');
const path = require('path');
const {
  default: makeWASocket,
  useMultiFileAuthState,
  downloadMediaMessage,
  DisconnectReason,
} = require('@whiskeysockets/baileys');
const qrcode = require('qrcode');
const pino = require('pino');
const { HttpsProxyAgent } = require('https-proxy-agent');

const BOT_URL = process.env.BOT_URL || 'http://127.0.0.1:7860/gateway';
const QR_PNG = process.env.QR_PNG || path.join(__dirname, 'qr.png');
const STATUS_JSON = process.env.STATUS_JSON || path.join(__dirname, 'gateway-status.json');
const AUTH_DIR = process.env.AUTH_DIR || path.join(__dirname, 'auth');
const TRANSCRIBE_URL =
  process.env.TRANSCRIBE_URL || 'https://azilal-transcribe.warkff2009.workers.dev/transcribe';
const log = pino({ level: 'info' });

function setStatus(state, detail) {
  try {
    fs.writeFileSync(
      STATUS_JSON,
      JSON.stringify({ state, detail: detail || '', updated: new Date().toISOString() })
    );
  } catch (e) { /* best effort */ }
}

function clearQr() {
  try { fs.unlinkSync(QR_PNG); } catch (e) { /* already gone */ }
}

// Proxy agent, only when the environment routes through a proxy (some
// sandboxes do; the codespace has direct internet and skips this).
function makeAgent() {
  const proxy =
    process.env.HTTPS_PROXY || process.env.https_proxy ||
    process.env.HTTP_PROXY || process.env.http_proxy;
  if (!proxy) return undefined;
  try { return new HttpsProxyAgent(proxy); } catch (e) { return undefined; }
}

// Unwrap ephemeral / view-once containers to the real message.
function unwrap(m) {
  if (!m) return null;
  if (m.ephemeralMessage) return unwrap(m.ephemeralMessage.message);
  if (m.viewOnceMessage) return unwrap(m.viewOnceMessage.message);
  if (m.viewOnceMessageV2) return unwrap(m.viewOnceMessageV2.message);
  return m;
}

async function transcribeAudio(buf) {
  const r = await fetch(TRANSCRIBE_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'audio/ogg' },
    body: buf,
  });
  if (!r.ok) throw new Error('transcribe HTTP ' + r.status);
  const j = await r.json();
  return (j.text || '').trim();
}

async function callBot(from, text, mediaText) {
  const r = await fetch(BOT_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from, text: text || '', media_text: mediaText ?? null }),
  });
  if (!r.ok) throw new Error('bot HTTP ' + r.status);
  const j = await r.json();
  return j.actions || [];
}

// IDs of messages this gateway sent (to skip them when they echo back).
const sentIds = new Set();

async function executeActions(sock, jid, actions) {
  for (const act of actions) {
    if (act.type === 'text') {
      const sent = await sock.sendMessage(jid, { text: act.body });
      if (sent && sent.key && sent.key.id) sentIds.add(sent.key.id);
    } else if (act.type === 'files') {
      for (const [name, b64] of Object.entries(act.files || {})) {
        const sent = await sock.sendMessage(jid, {
          document: Buffer.from(b64, 'base64'),
          fileName: name,
          mimetype:
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        });
        if (sent && sent.key && sent.key.id) sentIds.add(sent.key.id);
      }
    }
  }
  if (sentIds.size > 1000) sentIds.clear();
}

// The linked number's digits, e.g. sock.user.id "212703758191:12@s.whatsapp.net".
// Read at message time: sock.user is empty until after QR scan + connect.
async function handleIncoming(sock, msg) {
  const remoteJid = msg.key.remoteJid;
  if (!remoteJid) return;
  const own = sock.user && sock.user.id
    ? sock.user.id.split(':')[0].split('@')[0]
    : '';
  if (
    remoteJid === 'status@broadcast' ||
    remoteJid.endsWith('@g.us') ||
    remoteJid.endsWith('@broadcast')
  ) return; // status + groups are not bot chats
  const sender = remoteJid.replace('@s.whatsapp.net', '').replace('@lid', '');
  if (msg.key.fromMe) {
    // Only the self-chat, and only messages the phone sent (not the
    // gateway's own replies echoing back).
    if (sender !== own || sentIds.has(msg.key.id)) return;
  } else if (sender !== own) {
    // Anyone else messaging this number must NOT get bot replies.
    return;
  }
  const content = unwrap(msg.message);
  if (!content) return;
  try {
    if (content.audioMessage) {
      // Voice note -> Groq transcription -> bot treats it as typed text.
      const buf = await downloadMediaMessage(msg, 'buffer', {}, { logger: log });
      let text = '';
      try { text = await transcribeAudio(buf); } catch (e) { text = ''; }
      if (!text) {
        await sock.sendMessage(remoteJid, {
          text: 'ما فهمتش الصوت — عاود صيفطو ولا كتب الدروس.',
        });
        return;
      }
      await executeActions(sock, remoteJid, await callBot(sender, '', text));
      return;
    }
    if (content.imageMessage) {
      const caption = (content.imageMessage.caption || '').trim();
      if (caption) {
        await executeActions(sock, remoteJid, await callBot(sender, caption, null));
      } else {
        await sock.sendMessage(remoteJid, {
          text: 'وصلاتني صورة، ولكن قراءة الصور ما مفعلةش دابا — صيفط الدروس كتابةً أو صوتاً 🎤',
        });
      }
      return;
    }
    if (content.documentMessage || content.videoMessage) {
      await sock.sendMessage(remoteJid, {
        text: 'وصلني ملف، ولكن كنحتاج الدروس كتابةً أو صورة أو صوتاً 🎤',
      });
      return;
    }
    const text =
      content.conversation ||
      (content.extendedTextMessage && content.extendedTextMessage.text) ||
      '';
    if (!text.trim()) return;
    await executeActions(sock, remoteJid, await callBot(sender, text, null));
  } catch (e) {
    log.error(e);
    try {
      await sock.sendMessage(remoteJid, { text: 'وقع مشكل: ' + (e.message || e) });
    } catch (_) { /* reply failed too */ }
  }
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    logger: pino({ level: 'silent' }),
    agent: makeAgent(),
  });
  // NOTE: the own number is read inside handleIncoming, not here —
  // sock.user is empty until after the QR scan + connection open.
  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', async (u) => {
    const { connection, qr, lastDisconnect } = u;
    if (qr) {
      try {
        await qrcode.toFile(QR_PNG, qr);
        setStatus('qr', 'scan');
        log.info('QR ready');
      } catch (e) { log.error(e); }
    }
    if (connection === 'open') {
      clearQr();
      setStatus('connected', '');
      log.info('gateway connected');
    }
    if (connection === 'close') {
      const code =
        lastDisconnect &&
        lastDisconnect.error &&
        lastDisconnect.error.output &&
        lastDisconnect.error.output.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;
      setStatus(loggedOut ? 'logged_out' : 'disconnected', '');
      if (loggedOut) {
        // Session revoked from the phone: drop creds so the next start
        // generates a fresh QR instead of looping on a dead session.
        try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (e) {}
        clearQr();
      } else {
        setTimeout(start, 5000);
      }
    }
  });
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return; // ignore history sync on (re)connect
    for (const m of messages) {
      await handleIncoming(sock, m);
    }
  });
}

start().catch((e) => {
  log.error(e);
  setStatus('error', String(e && e.message ? e.message : e));
  process.exit(1);
});
