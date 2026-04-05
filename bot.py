"""
Craftical CRM — Standalone WhatsApp Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Connects REMOTELY to Hostinger MySQL database.
Runs locally on your Windows PC with Chrome.

Features:
  1. Reads message_queue table → sends outgoing WhatsApp messages
  2. Listens for incoming messages → saves to chat_logs table
  3. AI-powered auto-replies via Groq (Llama 3.1)
  4. Media download & upload support
  5. Heartbeat status for CRM dashboard
  6. Chat sync on startup

Usage:
  1. pip install selenium webdriver-manager mysql-connector-python requests python-dotenv
  2. Create .env file with your credentials (see below)
  3. python bot.py

Required .env file:
  DB_HOST=your-hostinger-ip-or-hostname
  DB_NAME=u740475852_crmdb
  DB_USER=u740475852_crmadmonline
  DB_PASS=Maruti@8077
  DB_PORT=3306
  GROQ_API_KEY=your-groq-key-here   (optional, for AI replies)
"""

import os
import re
import sys
import json
import time
import base64
import mimetypes
import logging
from datetime import datetime
from typing import Optional, Dict, Any

# ═══════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env loading is optional

# Database — Remote Hostinger MySQL
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'u740475852_crmdb'),
    'user': os.getenv('DB_USER', 'u740475852_crmadmonline'),
    'password': os.getenv('DB_PASS', 'Maruti@8077'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'charset': 'utf8mb4',
    'autocommit': False,
    'connection_timeout': 30,
}

# Business Details
BUSINESS_NAME = "Craftical"
BUSINESS_LOCATION = "30/1, Kudlu Main Rd, AECS Layout - A Block, Near Siva Temple, Singasandra, Bengaluru, 560068"
CONTACT_PHONE = "+91 9873708890, +91 9873222344"
WEBSITE_URL = "https://www.upharjunction.com/"
BUSINESS_HOURS = "Mon-Sat 10:00 AM - 7:00 PM"
GST_NO = "29BVMPD0678R1ZP"
CATALOG_LINK = WEBSITE_URL

# AI (Groq — Free tier)
AI_API_KEY = os.getenv("GROQ_API_KEY")
AI_MODEL = "llama-3.1-8b-instant"
AI_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_PROMPT_CHARS = 6000

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "whatsapp_bot_state.json")
UPLOADS_DIR = os.path.join(SCRIPT_DIR, "uploads")
BRIDGE_INIT_JS = os.path.join(SCRIPT_DIR, "bridge_init.js")
WPP_JS = os.path.join(SCRIPT_DIR, "wppconnect-wa.js")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('Bot')

# ═══════════════════════════════════════════════════════════
# DATABASE (Remote MySQL)
# ═══════════════════════════════════════════════════════════

import mysql.connector

def get_db():
    """Get a fresh MySQL connection to remote Hostinger DB."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as e:
        log.error(f"DB connection failed: {e}")
        return None

# ═══════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"State save error: {e}")

def update_heartbeat(whatsapp_status="unknown"):
    state = load_state()
    state["_bot_status"] = {
        "last_active": _now_str(),
        "bot_process": "online",
        "whatsapp_status": whatsapp_status
    }
    save_state(state)

# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def _clean_phone(candidate: str) -> Optional[str]:
    """Normalize phone to 91-prefixed digits. Returns None if invalid."""
    if not candidate:
        return None
    digits = re.sub(r"\D", "", str(candidate))
    if not digits:
        return None
    if len(digits) > 13 or len(digits) < 7:
        return None
    if len(digits) == 10:
        return "91" + digits
    return digits

def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

INTENT_GREET = re.compile(r"\b(hi|hello|hey|namaste|good\s*morning)\b", re.I)
INTENT_CATALOG = re.compile(r"\b(catalog|product)\b", re.I)
INTENT_LOCATION = re.compile(r"\b(location|address)\b", re.I)
INTENT_RESET = re.compile(r"\b(reset|restart)\b", re.I)

# ═══════════════════════════════════════════════════════════
# CHAT LOGGING (Remote DB)
# ═══════════════════════════════════════════════════════════

def log_chat_db(lead_id, sender, message, media_path=None, timestamp=None):
    conn = get_db()
    if not conn:
        log.error(f"[LOG] No DB for lead={lead_id}")
        return
    try:
        cursor = conn.cursor()
        ts_val = timestamp or _now_str()
        try:
            cursor.execute(
                "INSERT INTO chat_logs (lead_id, sender, message, media_path, timestamp) VALUES (%s, %s, %s, %s, %s)",
                (lead_id, sender, message, media_path, ts_val)
            )
        except Exception:
            cursor.execute(
                "INSERT INTO chat_logs (lead_id, sender, message, timestamp) VALUES (%s, %s, %s, %s)",
                (lead_id, sender, message, ts_val)
            )
        conn.commit()
        log.info(f"[LOG] lead={lead_id} sender={sender} msg={message[:40]}...")
    except Exception as e:
        log.error(f"[LOG] lead={lead_id}: {e}")
    finally:
        conn.close()

# ═══════════════════════════════════════════════════════════
# WELCOME & PATTERN REPLIES
# ═══════════════════════════════════════════════════════════

def build_welcome_message(name: str = "") -> str:
    skip_names = {"", "customer", "unknown", "whatsapp lead", "new lead"}
    display_name = name.strip() if name and name.strip().lower() not in skip_names else ""
    greeting = f"Namaste {display_name}! 🙏" if display_name else "Namaste! 🙏"
    return (
        f"{greeting}\n"
        f"I'm *Juliana* from *{BUSINESS_NAME}* 😊\n\n"
        f"We are *manufacturers based in Bangalore*, specializing in fully *customised corporate gifts* — "
        f"Desk Organisers, Trophies, Table Calendars, Fridge Magnets, Keychains, Mugs, "
        f"T-shirts, Wall Clocks, Table Clocks & more — all made to your design!\n\n"
        f"How can I help you today? Feel free to share what you're looking for, "
        f"or browse our catalog 👉 {WEBSITE_URL}"
    )

def decide_reply(chat_id, text, state):
    text = (text or "").lower()
    if chat_id not in state:
        state[chat_id] = {}
    chat_state = state[chat_id]

    last_processed = chat_state.get('last_processed_msg')
    if last_processed == text:
        return None
    chat_state['last_processed_msg'] = text
    save_state(state)

    if INTENT_RESET.search(text):
        chat_state.clear()
        save_state(state)
        return "Conversation reset."

    if INTENT_GREET.search(text):
        last_greet = chat_state.get('last_greet_time')
        if last_greet:
            try:
                last_dt = datetime.strptime(last_greet, "%Y-%m-%d %H:%M:%S")
                if (datetime.now() - last_dt).total_seconds() < 24 * 3600:
                    return None
            except Exception:
                pass
        chat_state['last_greet_time'] = _now_str()
        save_state(state)
        return build_welcome_message()

    if "about" in text or "craftical" in text:
        return (
            f"*About {BUSINESS_NAME}*\n\n"
            f"We are *manufacturers based in Bangalore*, specializing in fully customised corporate gifts and promotional products.\n"
            f"📍 {BUSINESS_LOCATION}\n"
            f"📞 {CONTACT_PHONE}\n"
            f"🕐 {BUSINESS_HOURS}\n\n"
            "What product are you interested in?"
        )

    if INTENT_CATALOG.search(text) or "product" in text:
        return (
            "*Our Products* (all 100% customisable)\n\n"
            "• Desk Organisers\n"
            "• Trophies (acrylic, MDF, metal)\n"
            "• Table Calendars & Wooden Desk Calendars\n"
            "• Fridge Magnets & Keychains\n"
            "• Mugs & T-shirts\n"
            "• Wall Clocks & Table Clocks\n"
            "• Corporate Gift Hampers\n\n"
            f"Browse our full catalogue 👉 {CATALOG_LINK}\n"
            "Which product interests you? I'd love to prepare a quote!"
        )

    if INTENT_LOCATION.search(text):
        return f"Address: {BUSINESS_LOCATION}"

    return None

# ═══════════════════════════════════════════════════════════
# AI REPLIES (Groq — Llama 3.1)
# ═══════════════════════════════════════════════════════════

BUSINESS_SYSTEM_PROMPT = f"""You are Juliana, a warm and professional WhatsApp sales executive at {BUSINESS_NAME}.

Business Details:
- Name: {BUSINESS_NAME}
- Address: {BUSINESS_LOCATION}
- Phone: {CONTACT_PHONE}
- Website: {WEBSITE_URL}
- Hours: {BUSINESS_HOURS}
- GST: {GST_NO}

Products & Services (all 100% customisable as per customer design/branding):
- Desk Organisers, Trophies (acrylic, MDF, metal)
- Table Calendars & Wooden Table Top Desk Calendars
- Fridge Magnets, Keychains, Mugs, T-shirts
- Wall Clocks & Table Clocks, Corporate gift hampers
- UV printing, laser cutting & engraving available
- Bulk orders welcome — special pricing for 50+ units

Your Sales Personality:
- Greet warmly, be helpful and conversational
- The ONLY mandatory info needed is: *product name* and *quantity*
- Never quote a price — always say "I'll prepare a custom quote for you"
- If they ask for catalog, share: {WEBSITE_URL}
- Reply in *English* by default. Only switch if customer explicitly asks
- Keep replies to 2-3 lines max — short and punchy
- Use *bold* for key points
- Never reveal you are an AI
- For complaints, say "Let me connect you with our team right away"

Goal: Understand what the customer needs, collect their requirements, and offer to send a custom quote."""

def get_ai_reply(chat_id, text, lead_id=None):
    if not AI_API_KEY:
        return None
    try:
        import requests as http_requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        messages = [{"role": "system", "content": BUSINESS_SYSTEM_PROMPT}]
        total_chars = len(BUSINESS_SYSTEM_PROMPT)

        # Get recent chat history from remote DB
        history_msgs = []
        if lead_id:
            conn = get_db()
            if conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT sender, message FROM chat_logs WHERE lead_id = %s ORDER BY timestamp DESC LIMIT 4",
                    (lead_id,)
                )
                rows = cursor.fetchall()
                conn.close()
                for row in reversed(rows):
                    msg_text = (row[1] or "")[:300]
                    if row[0] in ("Lead", "Customer"):
                        history_msgs.append({"role": "user", "content": msg_text})
                    else:
                        history_msgs.append({"role": "assistant", "content": msg_text})
                    total_chars += len(msg_text)

        if total_chars + len(text) < MAX_PROMPT_CHARS:
            messages.extend(history_msgs)

        messages.append({"role": "user", "content": text[:1000]})

        resp = http_requests.post(
            AI_URL,
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
            json={"model": AI_MODEL, "messages": messages, "max_tokens": 150, "temperature": 0.7},
            timeout=15, verify=False
        )

        if resp.status_code == 200:
            reply = resp.json()["choices"][0]["message"]["content"].strip()
            if reply:
                log.info(f"[AI] Reply: {reply[:60]}...")
            return reply
        elif resp.status_code in (413, 429):
            return "Ji, I've noted your message. Our team will get back to you shortly! 🙏"
        else:
            log.error(f"[AI] Status {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        log.error(f"[AI] {e}")
        return None

# ═══════════════════════════════════════════════════════════
# MEDIA HELPERS
# ═══════════════════════════════════════════════════════════

def download_media(driver, msg_id, lead_id, timestamp) -> Optional[str]:
    try:
        result = driver.execute_async_script(f"""
            const callback = arguments[arguments.length - 1];
            WPP.chat.downloadMedia('{msg_id}')
                .then(base64 => callback(base64))
                .catch(err => callback(null));
        """)
        if not result:
            return None
        header, encoded = result.split(",", 1)
        mime_type = header.split(";")[0].split(":")[1]
        ext = mimetypes.guess_extension(mime_type) or ".bin"
        lead_dir = os.path.join(UPLOADS_DIR, str(lead_id))
        os.makedirs(lead_dir, exist_ok=True)
        filename = f"{timestamp}_{msg_id[:8]}{ext}".replace(":", "-")
        filepath = os.path.join(lead_dir, filename)
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(encoded))
        log.info(f"[MEDIA] Saved → {filepath}")
        return filepath
    except Exception as e:
        log.error(f"[MEDIA] Download error: {e}")
        return None

def save_wa_media(driver, msg_id, media_type, lead_id):
    try:
        uploads_dir = os.path.join(UPLOADS_DIR, "whatsapp")
        os.makedirs(uploads_dir, exist_ok=True)
        b64_data = driver.execute_async_script("""
            const callback = arguments[arguments.length - 1];
            const msgId = arguments[0];
            async function dl() {
                try {
                    if (typeof WPP !== 'undefined' && WPP.chat && WPP.chat.downloadMedia) {
                        const r = await WPP.chat.downloadMedia(msgId);
                        if (r) return typeof r === 'string' ? r : r.data || null;
                    }
                } catch(e) {}
                try {
                    const WA = WPP.whatsapp || {};
                    const ms = WA.MsgStore || (window.Store && window.Store.Msg);
                    if (ms) { const m = ms.get(msgId); if (m && m.downloadMedia) { const b = await m.downloadMedia(); if (b) return new Promise(r => { const rd = new FileReader(); rd.onloadend = () => r(rd.result); rd.readAsDataURL(b); }); } }
                } catch(e) {}
                return null;
            }
            dl().then(callback).catch(() => callback(null));
        """, msg_id)
        if not b64_data:
            return None
        ext = 'jpg' if 'image' in (media_type or '') else ('mp4' if 'video' in (media_type or '') else 'bin')
        clean_id = re.sub(r'[^\w]', '_', str(msg_id)[:20])
        filename = f"wa_{lead_id}_{clean_id}.{ext}"
        filepath = os.path.join(uploads_dir, filename)
        if ',' in b64_data:
            b64_data = b64_data.split(',')[1]
        with open(filepath, 'wb') as f:
            f.write(base64.b64decode(b64_data))
        rel_path = f"uploads/whatsapp/{filename}"
        log.info(f"[MEDIA] Saved {ext} → {rel_path}")
        return rel_path
    except Exception as e:
        log.error(f"[MEDIA] Download failed: {e}")
        return None

# ═══════════════════════════════════════════════════════════
# WPP BRIDGE (WhatsApp Web automation)
# ═══════════════════════════════════════════════════════════

def inject_bridge(driver):
    try:
        if driver.execute_script("return typeof WPP !== 'undefined' && window.WPP_READY"):
            return True
        driver.execute_script("window.wppForceMainLoad = true;")
        if os.path.exists(WPP_JS):
            with open(WPP_JS, "r", encoding="utf-8") as f:
                driver.execute_script(f.read())
        else:
            log.error(f"[BRIDGE] Missing {WPP_JS} — copy wppconnect-wa.js to bot directory!")
            return False
        if os.path.exists(BRIDGE_INIT_JS):
            with open(BRIDGE_INIT_JS, "r", encoding="utf-8") as f:
                driver.execute_script(f.read())
        else:
            log.error(f"[BRIDGE] Missing {BRIDGE_INIT_JS} — copy bridge_init.js to bot directory!")
            return False
        log.info("[BRIDGE] Waiting for WPP full init...")
        for i in range(40):
            if driver.execute_script("return window.WPP_READY === true && window.WPP_WEBPACK_FULLY_READY === true"):
                log.info("[BRIDGE] WPPConnect Bridge ready.")
                return True
            time.sleep(1)
        log.warning("[BRIDGE] Full init timeout.")
        return True
    except Exception as e:
        log.error(f"[BRIDGE] {e}")
        return False

def send_via_bridge(driver, phone, message, lead_id=None):
    try:
        clean = _clean_phone(phone)
        if not clean:
            log.error(f"[SEND] Invalid phone: {phone}")
            return False
        chat_id = f"{clean}@c.us"
        log.info(f"[SEND] → {chat_id}: {message[:50]}...")
        result = driver.execute_async_script("""
            const chatId = arguments[0];
            const msg = arguments[1];
            const callback = arguments[arguments.length - 1];
            async function waitReady(ms) { const d = Date.now() + ms; while (Date.now() < d) { if (window.WPP_WEBPACK_FULLY_READY) return true; await new Promise(r => setTimeout(r, 300)); } return false; }
            async function trySend() {
                const errors = [];
                await waitReady(20000);
                try { const s = await WPP.chat.sendTextMessage(chatId, msg, {createChat: true}); if (s && s.id) return {ok: true, method: 'sendTextMessage'}; } catch(e) { errors.push(e.message); }
                try { const WA = WPP.whatsapp; if (WA && WA.functions && WA.functions.sendTextMsgToChat && WA.ChatStore) { const wid = WA.WidFactory.createWid(chatId); let chat = WA.ChatStore.get(wid); if (!chat) { chat = WA.ChatStore.gadd ? WA.ChatStore.gadd({id: wid}, {merge: true}) : WA.ChatStore.add({id: wid}); if (chat) await new Promise(r => setTimeout(r, 500)); } if (chat) { await WA.functions.sendTextMsgToChat(chat, msg); return {ok: true, method: 'sendTextMsgToChat'}; } } } catch(e) { errors.push(e.message); }
                try { if (WPP.contact) await WPP.contact.queryExists(chatId).catch(() => null); await new Promise(r => setTimeout(r, 1500)); const s2 = await WPP.chat.sendTextMessage(chatId, msg, {createChat: true}); if (s2 && s2.id) return {ok: true, method: 'queryExists+send'}; } catch(e) { errors.push(e.message); }
                return {ok: false, error: errors.join(' | ')};
            }
            trySend().then(callback).catch(err => callback({ok: false, error: err.message}));
        """, chat_id, message)
        if result and result.get('ok'):
            log.info(f"[SEND OK] {clean} via {result.get('method','?')}")
            if lead_id:
                log_chat_db(lead_id, 'Bot', message)
            return True
        else:
            log.error(f"[SEND FAIL] {clean}: {result.get('error','?') if result else 'no result'}")
            return False
    except Exception as e:
        log.error(f"[SEND ERROR] {e}")
        return False

# ═══════════════════════════════════════════════════════════
# CHAT SYNC
# ═══════════════════════════════════════════════════════════

def sync_chat_via_bridge(driver, chat_id, lead_id):
    """Pull messages from a specific WhatsApp chat into remote DB."""
    try:
        phone_only = chat_id.replace('@c.us', '').replace('@lid', '')
        phone_suffix = phone_only[-10:] if len(phone_only) >= 10 else phone_only
        log.info(f"[SYNC] Starting for {phone_only} (lead {lead_id})...")

        try:
            driver.execute_script("""
                try { if (typeof WPP !== 'undefined' && WPP.chat) { try { WPP.chat.openChatBottom(arguments[0] + '@c.us'); } catch(e) {} } } catch(e) {}
            """, phone_only)
            time.sleep(3)
        except Exception:
            pass

        find_result = driver.execute_script("""
            try {
                if (typeof WPP === 'undefined') return {status: 'no_wpp'};
                const cs = WPP.whatsapp.ChatStore; if (!cs) return {status: 'no_store'};
                const ps = arguments[0], po = arguments[1];
                let tc = null;
                const all = cs.getModelsArray ? cs.getModelsArray() : (cs._models || []);
                for (const c of all) { const cid = c.id ? (c.id._serialized || c.id.user || '') : ''; if (cid.includes(ps) || cid.includes(po)) { tc = c; break; } }
                if (!tc) tc = cs.get(po + '@c.us') || cs.get(po + '@s.whatsapp.net') || cs.get(po);
                if (!tc) return {status: 'chat_not_found', searched: all.length};
                return {status: 'found', chatId: tc.id ? tc.id._serialized : '', chatName: tc.name || tc.formattedTitle || '?', currentMsgs: tc.msgs ? (tc.msgs.length || 0) : 0};
            } catch(e) { return {status: 'error:' + e.message}; }
        """, phone_suffix, phone_only)

        if not find_result or find_result.get('status') != 'found':
            log.info(f"[SYNC] Chat not found for {phone_only}")
            return 0

        found_chat_id = find_result['chatId']
        log.info(f"[SYNC] Found '{find_result['chatName']}' ({found_chat_id})")

        # Load earlier messages
        try:
            driver.execute_async_script("""
                const callback = arguments[arguments.length - 1];
                try {
                    const cid = arguments[0];
                    if (typeof WPP !== 'undefined' && WPP.chat) try { WPP.chat.openChatBottom(cid); } catch(e) {}
                    await new Promise(r => setTimeout(r, 2000));
                    const cs = WPP.whatsapp.ChatStore; const chat = cs.get(cid);
                    if (chat && chat.msgs && chat.msgs.loadEarlierMsgs) { for (let i = 0; i < 8; i++) { const b = chat.msgs.length || 0; try { await chat.msgs.loadEarlierMsgs(); } catch(e) { break; } await new Promise(r => setTimeout(r, 500)); if ((chat.msgs.length || 0) === b) break; } }
                    callback({loaded: chat && chat.msgs ? (chat.msgs.length || 0) : 0});
                } catch(e) { callback({error: e.message, loaded: 0}); }
            """, found_chat_id)
        except Exception:
            pass

        # Read messages
        msgs = driver.execute_script("""
            try {
                const cs = WPP.whatsapp.ChatStore; if (!cs) return [];
                const chat = cs.get(arguments[0]); if (!chat || !chat.msgs) return [];
                const models = chat.msgs.getModelsArray ? chat.msgs.getModelsArray() : (chat.msgs._models || []);
                return models.slice(-500).map(m => ({
                    id: m.id ? m.id._serialized : '', body: m.body || m.caption || '',
                    fromMe: m.id ? m.id.fromMe : false, t: m.t || 0,
                    type: m.type || 'chat', mimetype: m.mimetype || '', isMedia: m.isMedia || m.isMMS || false
                }));
            } catch(e) { return []; }
        """, found_chat_id)

        if not msgs:
            return 0

        # Save to remote DB
        conn = get_db()
        if not conn:
            return 0
        cursor = conn.cursor()
        cursor.execute("SELECT message, sender, timestamp FROM chat_logs WHERE lead_id = %s", (lead_id,))
        existing = set()
        for row in cursor.fetchall():
            ts_str = row[2].strftime("%Y-%m-%d %H:%M:%S") if row[2] else None
            existing.add((row[0], row[1], ts_str))

        JUNK = [
            re.compile(r'^\d{1,2}:\d{2}\s*(am|pm)?\s*$', re.I),
            re.compile(r'^Messages and calls are end-to-end encrypted', re.I),
            re.compile(r'^This message was deleted', re.I),
            re.compile(r'^You deleted this message', re.I),
            re.compile(r'^Waiting for this message', re.I),
            re.compile(r'^/9j/[A-Za-z0-9+/]'),
            re.compile(r'^[A-Za-z0-9+/]{40,}={0,2}$'),
        ]
        SKIP_TYPES = {'e2e_notification', 'notification_template', 'gp2', 'ciphertext', 'revoked', 'protocol', 'call_log', 'notification'}

        count = 0
        for m in msgs:
            body = m.get('body', '').strip()
            msg_type = m.get('type', 'chat')
            if body and len(body) > 100 and re.match(r'^[A-Za-z0-9+/]{40,}', body[:60]):
                body = f"[{msg_type.upper()}]" if m.get('isMedia') else ''
            if msg_type in SKIP_TYPES:
                continue
            if any(p.match(body) for p in JUNK):
                continue

            sender = "Lead" if not m.get('fromMe') else "Bot"
            ts_val = m.get('t', 0)
            ts_db = None
            if ts_val and ts_val > 0:
                try:
                    ts_db = datetime.fromtimestamp(ts_val).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            media_path = None
            if (m.get('isMedia') or msg_type in ('image', 'video', 'sticker', 'ptt', 'audio', 'document')) and m.get('id'):
                media_path = save_wa_media(driver, m['id'], m.get('mimetype', ''), lead_id)
                if not body:
                    body = f"[{msg_type.upper()}]"
            if not body and not media_path:
                continue
            if (body, sender, ts_db) not in existing:
                try:
                    cursor.execute(
                        "INSERT INTO chat_logs (lead_id, sender, message, timestamp, media_path) VALUES (%s, %s, %s, %s, %s)",
                        (lead_id, sender, body, ts_db, media_path))
                    existing.add((body, sender, ts_db))
                    count += 1
                except Exception as e:
                    log.error(f"[SYNC] Insert error: {e}")
        conn.commit()
        conn.close()
        log.info(f"[SYNC] {phone_only}: ✅ {count} new messages")
        return count
    except Exception as e:
        log.error(f"[SYNC ERROR] {chat_id}: {e}")
        return 0

def sync_all_leads_on_startup(driver):
    """Sync recent leads' WhatsApp chats into remote DB."""
    try:
        log.info("[STARTUP SYNC] Syncing recent leads...")
        conn = get_db()
        if not conn:
            return
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, phone FROM leads
            WHERE (updated_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            OR created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY))
            ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 10
        """)
        leads = cursor.fetchall()
        conn.close()
        log.info(f"[STARTUP SYNC] {len(leads)} leads to sync")
        synced = 0
        for lead in leads:
            clean = _clean_phone(lead[2])
            if clean:
                result = sync_chat_via_bridge(driver, f"{clean}@c.us", lead[0])
                if result and result > 0:
                    synced += 1
                time.sleep(2)
        log.info(f"[STARTUP SYNC] ✅ {synced} leads synced")
    except Exception as e:
        log.error(f"[STARTUP SYNC] {e}")

# ═══════════════════════════════════════════════════════════
# MAIN BOT LOOP
# ═══════════════════════════════════════════════════════════

def start_bot():
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.common.by import By

    log.info("=" * 60)
    log.info(f"  {BUSINESS_NAME} WhatsApp Bot — Starting")
    log.info(f"  Remote DB: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    log.info("=" * 60)

    # Test DB connection first
    test_conn = get_db()
    if not test_conn:
        log.error("❌ Cannot connect to remote database! Check credentials and Remote MySQL settings.")
        log.error(f"   Host: {DB_CONFIG['host']}, User: {DB_CONFIG['user']}, DB: {DB_CONFIG['database']}")
        return
    test_conn.close()
    log.info("✅ Remote database connection OK")

    session_dir = os.path.join(os.getcwd(), 'chrome_data')
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    os.environ['WDM_SSL_VERIFY'] = '0'
    os.environ['WDM_LOCAL'] = '1'

    # Kill leftover chromedriver
    import subprocess as sp
    try:
        sp.call("taskkill /F /IM chromedriver.exe /T", shell=True, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        time.sleep(2)
    except Exception:
        pass

    options = webdriver.ChromeOptions()
    options.add_argument(f"user-data-dir={session_dir}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--start-minimized")
    options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = None
    for attempt in range(2):
        try:
            log.info(f"[INIT] Chrome attempt {attempt + 1}...")
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            log.info("[INIT] Chrome started!")
            break
        except Exception as e:
            log.error(f"[INIT] Chrome failed: {e}")
            if attempt == 0 and "crashed" in str(e).lower():
                try:
                    sp.call("taskkill /F /IM chromedriver.exe /T", shell=True, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
                    time.sleep(3)
                    import shutil
                    if os.path.exists(session_dir):
                        shutil.rmtree(session_dir, ignore_errors=True)
                    os.makedirs(session_dir, exist_ok=True)
                except Exception:
                    pass
            else:
                return

    if not driver:
        log.error("[CRITICAL] Could not start Chrome.")
        return

    driver.get("https://web.whatsapp.com")
    log.info("Waiting for WhatsApp login...")

    qr_path = os.path.join(os.getcwd(), "static", "whatsapp_qr.png")
    os.makedirs(os.path.dirname(qr_path), exist_ok=True)

    for _ in range(120):
        update_heartbeat(whatsapp_status="waiting_for_qr")
        if len(driver.find_elements(By.ID, "pane-side")) > 0:
            log.info("✅ WhatsApp Connected!")
            if os.path.exists(qr_path):
                try:
                    os.remove(qr_path)
                except Exception:
                    pass
            break
        try:
            qr_canvas = driver.find_elements(By.TAG_NAME, "canvas")
            if qr_canvas:
                qr_canvas[0].screenshot(qr_path)
            else:
                qr_divs = driver.find_elements(By.CSS_SELECTOR, "div[data-ref]")
                if qr_divs:
                    qr_divs[0].screenshot(qr_path)
                else:
                    driver.save_screenshot(qr_path)
        except Exception:
            pass
        time.sleep(5)

    inject_bridge(driver)
    log.info("[INIT] Waiting 10s for WhatsApp Web to load chats...")
    time.sleep(10)
    sync_all_leads_on_startup(driver)

    state = load_state()
    log.info("=" * 60)
    log.info("  ✅ Bot is LIVE — Monitoring messages & queue")
    log.info("=" * 60)

    # ── MAIN LOOP ──
    while True:
        try:
            update_heartbeat(whatsapp_status="authenticated")
            inject_bridge(driver)

            # ── POLL INCOMING EVENTS ──
            events = driver.execute_script("const ev = window.WPP_EVENTS || []; window.WPP_EVENTS = []; return ev;")
            if events:
                for ev in events:
                    if ev['type'] == 'new_message':
                        try:
                            chat_id = ev.get('chatId', ev['from'])
                            if 'status' in chat_id or 'broadcast' in chat_id:
                                continue
                            phone_only = chat_id.split('@')[0]
                            if not _clean_phone(phone_only):
                                continue

                            body = ev.get('body') or ev.get('caption') or ''
                            is_media = ev.get('is_media', False)

                            if body and len(body) > 50 and re.match(r'^[A-Za-z0-9+/]{40,}[=]{0,2}$', body[:60]):
                                is_media = True
                                body = ''

                            msg_id = ev['id']
                            ts = ev['timestamp']
                            from_me = ev.get('fromMe', False)

                            # Find lead in remote DB
                            conn = get_db()
                            if not conn:
                                continue
                            cursor = conn.cursor()
                            cursor.execute("SELECT id FROM leads WHERE phone LIKE %s", (f"%{phone_only[-10:]}",))
                            row = cursor.fetchone()

                            if not row:
                                if not from_me:
                                    log.info(f"[SKIP] Unknown: {phone_only}")
                                conn.close()
                                continue

                            lead_id = row[0]
                            cursor.execute("SELECT name FROM leads WHERE id = %s", (lead_id,))
                            name_row = cursor.fetchone()
                            lead_name = (name_row[0] if name_row and name_row[0] else 'Customer')

                            # Media handling
                            media_path = None
                            if is_media and not from_me:
                                media_path = download_media(driver, msg_id, lead_id, ts)
                                if media_path:
                                    rel_path = os.path.relpath(media_path, os.getcwd())
                                    cursor.execute("INSERT INTO documents (lead_id, file_type, file_path) VALUES (%s, %s, %s)",
                                                   (lead_id, ev.get('mimetype', 'unknown'), rel_path))
                                    conn.commit()
                                    body = f"[MEDIA] {ev.get('caption') or 'File Attachment'}"

                            # Log to remote DB
                            if from_me:
                                cursor.execute("""
                                    SELECT cl.id FROM chat_logs cl
                                    JOIN leads l ON cl.lead_id = l.id
                                    WHERE l.phone LIKE %s AND cl.message = %s
                                    AND cl.sender IN ('Bot', 'System')
                                    AND cl.timestamp >= NOW() - INTERVAL 5 MINUTE
                                    ORDER BY cl.timestamp DESC LIMIT 1
                                """, (f"%{phone_only[-10:]}", body))
                                if not cursor.fetchone():
                                    log_chat_db(lead_id, 'Bot', body, media_path=media_path)
                            else:
                                log.info(f"[IN] {phone_only}: {body[:40]}")
                                log_chat_db(lead_id, 'Lead', body)

                                # AI / Pattern reply
                                reply = decide_reply(phone_only, body, state)
                                if not reply:
                                    reply = get_ai_reply(phone_only, body, lead_id=lead_id)
                                if reply:
                                    log_chat_db(lead_id, 'Bot', reply)
                                    send_via_bridge(driver, phone_only, reply)

                            conn.close()
                        except Exception as e:
                            log.error(f"[EVENT] {e}")

            # ── PROCESS OUTGOING QUEUE (from PHP CRM) ──
            conn = get_db()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, phone, message, status, lead_id FROM message_queue WHERE status IN ('pending', 'sync_request') LIMIT 1")
                q_item = cursor.fetchone()
                if q_item:
                    qid, qphone, qmsg, qstatus, qlead = q_item
                    if qstatus == 'sync_request':
                        log.info(f"[QUEUE] Sync request for lead #{qlead}")
                        sync_chat_via_bridge(driver, f"{_clean_phone(qphone)}@c.us", qlead)
                        cursor.execute("UPDATE message_queue SET status = 'sent' WHERE id = %s", (qid,))
                    else:
                        # Try to parse as JSON (file send)
                        try:
                            msg_data = json.loads(qmsg)
                            if msg_data.get('type') == 'send_file':
                                file_path = msg_data.get('file_path', '')
                                caption = msg_data.get('caption', '')
                                filename = msg_data.get('filename', 'file')
                                mimetype = msg_data.get('mimetype', '')
                                if os.path.exists(file_path):
                                    clean = _clean_phone(qphone)
                                    chat_id_send = f"{clean}@c.us"
                                    with open(file_path, 'rb') as f:
                                        file_data = base64.b64encode(f.read()).decode('utf-8')
                                    if not mimetype:
                                        ext = os.path.splitext(filename)[1].lower()
                                        mime_map = {'.jpg': 'image/jpeg', '.png': 'image/png', '.pdf': 'application/pdf', '.mp4': 'video/mp4'}
                                        mimetype = mime_map.get(ext, 'application/octet-stream')
                                    data_uri = f"data:{mimetype};base64,{file_data}"
                                    result = driver.execute_async_script("""
                                        const callback = arguments[arguments.length - 1];
                                        try {
                                            const sent = await WPP.chat.sendFileMessage(arguments[0], arguments[1], {type: 'auto-detect', caption: arguments[2] || undefined, filename: arguments[3], createChat: true});
                                            callback({ok: true});
                                        } catch(e) { callback({ok: false, error: e.message}); }
                                    """, chat_id_send, data_uri, caption, filename)
                                    status = 'sent' if result and result.get('ok') else 'failed'
                                    cursor.execute(f"UPDATE message_queue SET status = %s WHERE id = %s", (status, qid))
                                else:
                                    cursor.execute("UPDATE message_queue SET status = 'failed' WHERE id = %s", (qid,))
                            else:
                                status = 'sent' if send_via_bridge(driver, qphone, qmsg) else 'failed'
                                cursor.execute("UPDATE message_queue SET status = %s WHERE id = %s", (status, qid))
                        except (json.JSONDecodeError, ValueError):
                            # Regular text message
                            status = 'sent' if send_via_bridge(driver, qphone, qmsg) else 'failed'
                            cursor.execute("UPDATE message_queue SET status = %s WHERE id = %s", (status, qid))
                    conn.commit()
                conn.close()

            time.sleep(2)

        except Exception as e:
            error_msg = str(e).lower()
            if any(x in error_msg for x in ['invalid session id', 'no such session', 'session not created', 'no such window', 'web view not found']):
                log.error("[SESSION LOST] Chrome died — restarting...")
                update_heartbeat(whatsapp_status="restarting")
                try:
                    driver.quit()
                except Exception:
                    pass
                time.sleep(3)
                start_bot()
                return
            else:
                log.error(f"[LOOP] {e}")
                time.sleep(5)


if __name__ == "__main__":
    try:
        os.system("taskkill /F /IM chromedriver.exe /T >nul 2>&1")
    except Exception:
        pass
    start_bot()
