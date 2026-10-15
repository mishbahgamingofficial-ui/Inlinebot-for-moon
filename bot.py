import os
import re
import time
import json
import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import (
    InputPeerUser,
    UpdateBotChatInviteRequester,
    PeerUser,
    PeerChannel,
)
from telethon.errors import (
    UserIsBlockedError,
    FloodWaitError,
    UserNotParticipantError,
    PeerIdInvalidError,
    ChatInvalidError,
    ChannelInvalidError,
    MessageIdInvalidError,
)

# ------------------ ENVIRONMENT ------------------
load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ProBotVIP")

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 10000))
SESSION_STRING = os.environ.get("SESSION_STRING", "")

BOT_USERNAME = "Tgtomemberhelp_bot"
START_TIME = time.time()

def parse_admin_ids(env_string):
    ids = []
    for uid in env_string.split(","):
        uid = uid.strip()
        if uid.lstrip('-').isdigit():
            ids.append(int(uid))
    return ids

ROOT_ADMINS = parse_admin_ids(os.environ.get("ADMIN_IDS", "0"))
ADMIN_IDS = list(ROOT_ADMINS)

# ------------------ DATABASE & STATE ------------------
DB_PATH = Path("bot_data.db")

tracked_users: dict[int, int] = {}      
blocked_users: set[int] = set()
banned_users: set[int] = set()
saved_messages: dict[int, dict] = {}    
button_forwards: dict[str, list] = {}   

welcome_enabled = True
inline_menu_text = "💎 **PREMIUM VIP ACCESS** 💎\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n👇 *Select an exclusive module below:*" 
support_username = "@SAHILOFFICIAL_0"
force_channel = ""
banner_config = {"msg_id": 0, "chat_id": 0}
click_stats = {"prediction": 0, "hack": 0, "support": 0}

recently_welcomed: set[int] = set()
user_chat_state: dict[int, bool] = {}
admin_chat_state: dict[int, dict] = {}
auto_replies: dict[str, str] = {}
admin_msg_map: dict[tuple, list] = {} 

def get_uptime():
    seconds = int(time.time() - START_TIME)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    return f"{minutes}m {seconds}s"

def init_db():
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                access_hash INTEGER NOT NULL,
                blocked INTEGER DEFAULT 0,
                joined_date TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                step INTEGER PRIMARY KEY,
                msg_type TEXT,
                text TEXT,
                msg_id INTEGER,
                from_chat INTEGER
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS button_msgs (
                btn_key TEXT,
                msg_id INTEGER NOT NULL,
                from_chat INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auto_replies (
                keyword TEXT PRIMARY KEY,
                response TEXT
            );
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS banned (
                user_id INTEGER PRIMARY KEY
            );
        """)
        return conn

def load_from_db():
    global tracked_users, blocked_users, banned_users, saved_messages, button_forwards
    global welcome_enabled, inline_menu_text, support_username, force_channel, banner_config, click_stats, auto_replies, ADMIN_IDS
    
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.cursor()
        
        cur.execute("SELECT user_id FROM admins")
        for (uid,) in cur.fetchall():
            if uid not in ADMIN_IDS:
                ADMIN_IDS.append(uid)

        cur.execute("SELECT user_id FROM banned")
        for (uid,) in cur.fetchall():
            banned_users.add(uid)

        cur.execute("SELECT user_id, access_hash, blocked FROM users")
        for uid, hsh, blk in cur.fetchall():
            tracked_users[uid] = hsh
            if blk:
                blocked_users.add(uid)
                
        cur.execute("SELECT step, msg_type, text, msg_id, from_chat FROM messages")
        for step, mtype, text, mid, fchat in cur.fetchall():
            saved_messages[step] = {
                "type": mtype, "text": text, "msg_id": mid, "from_chat": fchat
            }
            
        cur.execute("SELECT btn_key, msg_id, from_chat FROM button_msgs")
        button_forwards.clear()
        for key, mid, fchat in cur.fetchall():
            if key not in button_forwards:
                button_forwards[key] = []
            button_forwards[key].append({"msg_id": mid, "from_chat": fchat})
            
        cur.execute("SELECT key, value FROM settings")
        for k, v in cur.fetchall():
            if k == 'welcome_enabled':
                welcome_enabled = (v == "1")
            elif k == 'inline_text':
                inline_menu_text = v
            elif k == 'support_username':
                support_username = v
            elif k == 'force_channel':
                force_channel = v
            elif k == 'banner_config':
                try: banner_config = json.loads(v)
                except: pass
            elif k == 'click_stats':
                try: click_stats = json.loads(v)
                except: pass
            
        cur.execute("SELECT keyword, response FROM auto_replies")
        for kw, resp in cur.fetchall():
            auto_replies[kw] = resp
            
    logger.info(f"Loaded {len(tracked_users)} users, {len(auto_replies)} auto-replies, {len(ADMIN_IDS)} admins.")

def save_user(uid: int, access_hash: int, blocked: bool = False):
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (user_id, access_hash, blocked, joined_date) VALUES (?,?,?,?)",
            (uid, access_hash, int(blocked), datetime.now().isoformat()),
        )

def save_message_step(step: int, data: dict):
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO messages (step, msg_type, text, msg_id, from_chat) VALUES (?,?,?,?,?)",
            (step, data.get("type"), data.get("text"), data.get("msg_id"), data.get("from_chat")),
        )

def add_button_config(key: str, msg_id: int, from_chat: int):
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO button_msgs (btn_key, msg_id, from_chat) VALUES (?,?,?)",
            (key, msg_id, from_chat),
        )

def delete_button_config(key: str):
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("DELETE FROM button_msgs WHERE btn_key=?", (key,))

def set_setting(key: str, value: str):
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))

def increment_click(key: str):
    if key in click_stats:
        click_stats[key] = click_stats.get(key, 0) + 1
        set_setting("click_stats", json.dumps(click_stats))

# ------------------ CLIENT ------------------
client = TelegramClient(
    StringSession(SESSION_STRING) if SESSION_STRING else "bot_session",
    API_ID,
    API_HASH,
)

async def web_handler(request):
    return web.Response(text=f"🟢 ProBot VIP is alive! | Uptime: {get_uptime()} | Users: {len(tracked_users)}")

async def start_web():
    app = web.Application()
    app.add_routes([web.get("/", web_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server on port {PORT}")

def admin_only(func):
    async def wrapper(event):
        if event.sender_id not in ADMIN_IDS:
            await event.reply("❌ `[Access Denied] Admin privileges required.`")
            return
        await func(event)
    return wrapper

# ------------------ FORCE JOIN CHECKER ------------------
async def is_user_joined(uid: int) -> bool:
    if not force_channel or uid in ADMIN_IDS:
        return True
    try:
        clean_ch = force_channel.lstrip("@")
        await client(GetParticipantRequest(channel=clean_ch, participant=uid))
        return True
    except UserNotParticipantError:
        return False
    except Exception as e:
        logger.warning(f"Force join check error ({force_channel}): {e}")
        return True # Fail-safe taaki bot ruk na jaye agar bot channel me admin nahi hai

async def send_force_join_prompt(event, payload_action: str):
    clean_ch = force_channel.lstrip("@")
    btns = [
        [Button.url("📢 Join VIP Channel", f"https://t.me/{clean_ch}")],
        [Button.url("🔄 Try Again", f"https://t.me/{BOT_USERNAME}?start={payload_action}")]
    ]
    msg_text = (
        "⚠️ **ACCESS RESTRICTED** ⚠️\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "1. VIP Backup Channel - **Not Joined ❌**\n\n"
        "👉 *Please join our official channel below, then click the **Try Again** button to unlock this file!*"
    )
    await event.respond(msg_text, buttons=btns)

# ------------------ BAN / UNBAN / FORCE / BANNER COMMANDS ------------------
@client.on(events.NewMessage(pattern=r"^/ban\s+(\d+)"))
@admin_only
async def ban_user_cmd(event):
    uid = int(event.pattern_match.group(1))
    if uid in ADMIN_IDS:
        return await event.reply("❌ Admin ko ban nahi kar sakte!")
    banned_users.add(uid)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("INSERT OR IGNORE INTO banned (user_id) VALUES (?)", (uid,))
    await event.reply(f"🚫 **User `{uid}` has been BANNED from using the bot.**")

@client.on(events.NewMessage(pattern=r"^/unban\s+(\d+)"))
@admin_only
async def unban_user_cmd(event):
    uid = int(event.pattern_match.group(1))
    banned_users.discard(uid)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("DELETE FROM banned WHERE user_id=?", (uid,))
    await event.reply(f"✅ **User `{uid}` has been UNBANNED.**")

@client.on(events.NewMessage(pattern=r"^/setsupport\s+(.+)"))
@admin_only
async def set_support_cmd(event):
    global support_username
    support_username = event.pattern_match.group(1).strip()
    set_setting("support_username", support_username)
    await event.reply(f"✅ **Support Contact Updated:** `{support_username}`")

@client.on(events.NewMessage(pattern=r"^/setforce\s+(.+)"))
@admin_only
async def set_force_cmd(event):
    global force_channel
    ch = event.pattern_match.group(1).strip()
    if "t.me/" in ch:
        ch = ch.split("t.me/")[-1].split("/")[0]
    force_channel = ch.lstrip("@")
    set_setting("force_channel", force_channel)
    await event.reply(
        f"🔒 **Force Join Enabled for:** `@{force_channel}`\n\n"
        f"⚠️ *Note: Make sure this bot (`@{BOT_USERNAME}`) is added as an **Admin** in `@{force_channel}` so it can verify members!*"
    )

@client.on(events.NewMessage(pattern=r"^/clearforce$"))
@admin_only
async def clear_force_cmd(event):
    global force_channel
    force_channel = ""
    set_setting("force_channel", "")
    await event.reply("🔓 **Force Join Disabled.** All users can access modules directly.")

@client.on(events.NewMessage(pattern=r"^/setbanner$"))
@admin_only
async def set_banner_cmd(event):
    global banner_config
    if not event.is_reply:
        return await event.reply("❌ **Error:** Kisi Photo, Video ya GIF par reply karke `/setbanner` likho!")
    reply_msg = await event.get_reply_message()
    if not reply_msg.media:
        return await event.reply("❌ **Error:** Reply kiye gaye message mein koi Photo ya Video nahi hai!")
    
    banner_config = {"msg_id": reply_msg.id, "chat_id": event.chat_id}
    set_setting("banner_config", json.dumps(banner_config))
    await event.reply("🖼️ **VIP Menu Banner Saved Successfully!** Ab `/start` menu mein ye media upar dikhega.")

@client.on(events.NewMessage(pattern=r"^/clearbanner$"))
@admin_only
async def clear_banner_cmd(event):
    global banner_config
    banner_config = {"msg_id": 0, "chat_id": 0}
    set_setting("banner_config", json.dumps(banner_config))
    await event.reply("🗑 **Menu Banner Removed.** Ab sirf text aur buttons jayenge.")

@client.on(events.NewMessage(pattern=r"^/setinline(?:\s+(.+))?"))
@admin_only
async def set_inline_text(event):
    global inline_menu_text
    new_text = event.raw_text.replace('/setinline', '', 1).strip()
    
    if event.is_reply and not new_text:
        reply_msg = await event.get_reply_message()
        new_text = reply_msg.raw_text 
        
    if not new_text:
        return await event.reply("❌ **Error:** Ya toh text type karo `/setinline <text>` ya kisi message par reply karke `/setinline` likho.")
        
    inline_menu_text = new_text
    set_setting("inline_text", inline_menu_text)
    await event.reply(f"✅ **VIP Menu Text Updated!**\n\nPreview:\n{inline_menu_text}")

# ------------------ DYNAMIC ADMIN MANAGEMENT ------------------
@client.on(events.NewMessage(pattern=r"^/addadmin\s+(\d+)"))
@admin_only
async def add_admin(event):
    try:
        new_admin_id = int(event.pattern_match.group(1))
        if new_admin_id in ADMIN_IDS:
            return await event.reply(f"⚠️ **Notice:** User `{new_admin_id}` pehle se hi admin hai.")
        
        ADMIN_IDS.append(new_admin_id)
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_admin_id,))
        
        await event.reply(f"✅ **Success!** Naya admin add ho gaya hai.\n🆔 **UID:** `{new_admin_id}`")
        try:
            await client.send_message(new_admin_id, "🎉 **Congratulations!** Aapko is VIP bot ka Admin bana diya gaya hai. Apna panel open karne ke liye `/start` type karein.")
        except:
            pass
    except Exception as e:
        await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern=r"^/deladmin\s+(\d+)"))
@admin_only
async def del_admin(event):
    try:
        target_admin_id = int(event.pattern_match.group(1))
        if target_admin_id in ROOT_ADMINS:
            return await event.reply("❌ **Access Denied:** Ye user ek Root Admin (Env var) hai. Ise bot se remove nahi kiya ja sakta.")
        if target_admin_id not in ADMIN_IDS:
            return await event.reply(f"⚠️ User `{target_admin_id}` admin list mein nahi hai.")
        
        ADMIN_IDS.remove(target_admin_id)
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM admins WHERE user_id=?", (target_admin_id,))
        await event.reply(f"🗑 **Success!** Admin privileges hata di gayi hain.\n🆔 **UID:** `{target_admin_id}`")
    except Exception as e:
        await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern=r"^/listadmins"))
@admin_only
async def list_admins(event):
    msg = "👑 **CURRENT ADMINS:**\n➖➖➖➖➖➖➖➖➖➖➖➖\n"
    for aid in ADMIN_IDS:
        role = "⭐ (Root Admin)" if aid in ROOT_ADMINS else "🔹 (Sub Admin)"
        msg += f"{role} `{aid}`\n"
    msg += "➖➖➖➖➖➖➖➖➖➖➖➖\n👉 Naya add karne ke liye: `/addadmin ID`"
    await event.reply(msg)

# ------------------ ADMIN ALERTS ------------------
async def notify_admins_new_user(user):
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    full_name = f"{first_name} {last_name}".strip()
    safe_name = full_name.replace('[', '').replace(']', '').replace('*', '').replace('_', '').replace('`', '')
    if not safe_name.strip():
        safe_name = "Unknown User"
        
    linked_name = f"[{safe_name}](tg://user?id={user.id})"
    username_display = f"@{user.username}" if user.username else "❌ `[No Username]`"
    
    text = (
        f"🚨 **NEW VIP CONNECTION** 🚨\n"
        f"➖➖➖➖➖➖➖➖➖➖➖➖\n"
        f"👤 **Name:** {linked_name}\n"
        f"🔗 **Username:** {username_display}\n"
        f"🆔 **UID:** `{user.id}` *(Tap to copy)*\n"
        f"📊 **Total Users:** `{len(tracked_users)}`\n\n"
        f"💬 **Quick Action:** `/send {user.id} <msg>`\n"
        f"➖➖➖➖➖➖➖➖➖➖➖➖"
    )
    
    for admin_id in ADMIN_IDS:
        try:
            await client.send_message(admin_id, text)
        except Exception:
            pass

# ------------------ WELCOME SYSTEM & URL MENU ------------------
async def remove_welcome_cooldown(uid):
    await asyncio.sleep(30)
    recently_welcomed.discard(uid)

async def send_user_menu(entity):
    btns = [
        [Button.url("📊 START COLOUR TRADING", f"https://t.me/{BOT_USERNAME}?start=prediction")],
        [Button.url("⚡ GET NUMBER HACK", f"https://t.me/{BOT_USERNAME}?start=hack")],
        [Button.url("🎧 ADMIN SUPPORT", f"https://t.me/{BOT_USERNAME}?start=support")]
    ]
    try:
        # Check if Photo/Video Banner is set
        if banner_config.get("msg_id") and banner_config.get("chat_id"):
            try:
                b_msg = await client.get_messages(banner_config["chat_id"], ids=banner_config["msg_id"])
                if b_msg and b_msg.media:
                    await client.send_file(entity, b_msg.media, caption=inline_menu_text, buttons=btns)
                    return
            except Exception as banner_err:
                logger.warning(f"Banner load failed, falling back to text: {banner_err}")
                
        await client.send_message(entity, inline_menu_text, buttons=btns)
    except Exception as e:
        logger.error(f"Menu error: {e}")

async def send_welcome_sequence(user):
    uid = user.id
    if uid in recently_welcomed or uid in banned_users:
        return
    recently_welcomed.add(uid)

    if getattr(user, "is_bot", False):
        return

    try:
        full_user = await client.get_input_entity(uid)
        if not isinstance(full_user, InputPeerUser):
            return
        access_hash = full_user.access_hash
    except Exception:
        return

    is_new_user = uid not in tracked_users
    tracked_users[uid] = access_hash
    
    save_user(uid, access_hash, blocked=False)
    blocked_users.discard(uid)

    if is_new_user and uid not in ADMIN_IDS:
        asyncio.create_task(notify_admins_new_user(user))

    if not welcome_enabled or uid in ADMIN_IDS:
        asyncio.create_task(remove_welcome_cooldown(uid))
        return

    for step in sorted(saved_messages.keys()):
        item = saved_messages[step]
        try:
            if item["type"] == "forward":
                try:
                    await client.get_messages(item["from_chat"], ids=item["msg_id"])
                except Exception:
                    continue
                await client.forward_messages(full_user, item["msg_id"], item["from_chat"])
            else:
                await client.send_message(full_user, item["text"])
            await asyncio.sleep(0.5)
        except UserIsBlockedError:
            blocked_users.add(uid)
            save_user(uid, access_hash, blocked=True)
            return
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception:
            continue
            
    asyncio.create_task(remove_welcome_cooldown(uid))

@client.on(events.ChatAction())
async def chat_action(event):
    if event.user_joined or event.user_added:
        user = await event.get_user()
        if user and user.id not in banned_users:
            await send_welcome_sequence(user)
            if user.id not in ADMIN_IDS:
                await send_user_menu(user)

@client.on(events.Raw(types=UpdateBotChatInviteRequester))
async def join_request(event):
    try:
        if event.user_id in banned_users:
            return
        user = await client.get_entity(event.user_id)
        await send_welcome_sequence(user)
        if event.user_id not in ADMIN_IDS:
            await send_user_menu(user)
    except Exception:
        pass

# ------------------ START COMMAND & DEEP LINKING ROUTER ------------------
@client.on(events.NewMessage(pattern=r"(?i)^/start(?:\s+(.*))?$"))
async def start(event):
    if event.sender_id in banned_users:
        return await event.reply("🚫 **Access Denied:** You have been banned from using this bot.")

    payload = event.pattern_match.group(1)
    
    # URL BUTTON INTERCEPTOR
    if payload:
        payload = payload.strip().lower()
        if payload in ("hack", "prediction"):
            increment_click(payload)
            if not await is_user_joined(event.sender_id):
                await send_force_join_prompt(event, payload)
                return
            await send_button_forward(event, payload)
            return
        elif payload == "support":
            increment_click("support")
            if event.sender_id in ADMIN_IDS:
                return await event.reply("👑 You are an Admin! Support mode is for regular users.")
            user_chat_state[event.sender_id] = True
            clean_sup = support_username.lstrip("@")
            btns = [[Button.url("💬 Direct Chat with Admin", f"https://t.me/{clean_sup}")]]
            await event.respond(
                f"📝 **Premium Support Activated** 🟢\n"
                f"➖➖➖➖➖➖➖➖➖➖➖➖\n"
                f"👤 **Admin Contact ID:** {support_username}\n\n"
                f"✍️ *Aap yahan niche seedha apna message, photo ya screenshot bhej sakte hain, wo direct Admin ko deliver ho jayega!*",
                buttons=btns
            )
            return

    # NORMAL START LOGIC
    user_chat_state[event.sender_id] = False 
    admin_chat_state.pop(event.sender_id, None)
    
    if event.sender_id in blocked_users:
        blocked_users.discard(event.sender_id)
        access_hash = tracked_users.get(event.sender_id, 0)
        save_user(event.sender_id, access_hash, blocked=False)
    
    if event.sender_id in ADMIN_IDS:
        btns = [
            [Button.text("📊 Stats"), Button.text("⚙️ Status")],
            [Button.text("📢 Broadcast"), Button.text("✉️ Send Message")],
            [Button.text("🔢 Set Sequence"), Button.text("🔇 Toggle Welcome")],
            [Button.text("🖼️ Menu & Banner"), Button.text("🔒 Force Join")],
            [Button.text("🔘 Set Button"), Button.text("🗑 Clear Button")],
            [Button.text("📁 Backup"), Button.text("🔄 Restore")],
            [Button.text("👑 Manage Admins"), Button.text("🧹 Cleanup")]
        ]
        admin_panel_text = (
            "👑 **VIP ENTERPRISE ADMIN PANEL** 👑\n"
            "➖➖➖➖➖➖➖➖➖➖➖➖\n"
            f"🟢 **Uptime:** `{get_uptime()}` | 👥 **Users:** `{len(tracked_users)}`\n\n"
            "Select an action below to manage your bot:\n"
            "💡 *Quick Commands: `/setinline`, `/setbanner`, `/setforce`, `/setsupport`, `/ban`*"
        )
        await event.reply(admin_panel_text, buttons=btns)
    else:
        user = await event.get_sender()
        if user:
            await send_welcome_sequence(user)
            await send_user_menu(user)

# ------------------ FILE FORWARDING & ANIMATIONS ------------------
async def send_button_forward(event, key):
    uid = event.sender_id
    config_list = button_forwards.get(key)
    
    if not config_list:
        await event.respond("⚠️ `[Notice] This premium module is being updated. Please check back in a few minutes.`")
        return

    if key == "hack":
        status_msg = await event.respond("⚡️ `[ROOT] Initializing proxy server...`")
        await asyncio.sleep(0.4)
        await status_msg.edit("🌐 `[ROOT] Bypassing firewall matrix...`")
        await asyncio.sleep(0.4)
        await status_msg.edit("🔐 `[DECRYPT] Extracting VIP payload [██▓▓░░░░░░] 35%`")
        await asyncio.sleep(0.4)
        await status_msg.edit("📂 `[DECRYPT] Finalizing packages [██████████] 100%`")
        await asyncio.sleep(0.4)
        await status_msg.edit("✅ **SUCCESS:** VIP Package Extracted. Sending Securely... 🚀")
        await asyncio.sleep(0.5)
    elif key == "prediction":
        status_msg = await event.respond("🤖 `[AI] Booting Quantum Trading Engine...`")
        await asyncio.sleep(0.4)
        await status_msg.edit("📊 `[AI] Analyzing live market algorithms...`")
        await asyncio.sleep(0.4)
        await status_msg.edit("🔍 `[AI] Calculating high-win probabilities [██████▓▓░░] 80%`")
        await asyncio.sleep(0.4)
        await status_msg.edit("🎯 `[AI] Prediction locked [██████████] 100%`")
        await asyncio.sleep(0.4)
        await status_msg.edit("💎 **SIGNAL READY:** Forwarding exclusive details... 💸")
        await asyncio.sleep(0.5)
    else:
        status_msg = await event.respond("⏳ `[SYS] Processing secure request...`")

    try:
        sent_count = 0
        for config in config_list:
            admin_id = config["from_chat"]
            admin_hash = tracked_users.get(admin_id, 0)
            from_peer = InputPeerUser(admin_id, admin_hash) if (admin_id > 0 and admin_hash) else admin_id 
            try:
                await client.forward_messages(uid, config["msg_id"], from_peer)
                sent_count += 1
            except Exception as inner_e:
                logger.warning(f"Could not forward msg {config['msg_id']} for {uid}: {inner_e}")
            await asyncio.sleep(0.3) 
            
        if sent_count == 0:
            await status_msg.edit("❌ `[ERROR] The configured VIP files are currently unavailable.`")
        else:
            await asyncio.sleep(0.8)
            await status_msg.delete() 
            
    except UserIsBlockedError:
        blocked_users.add(uid)
        save_user(uid, tracked_users.get(uid, 0), blocked=True)
    except FloodWaitError as e:
        await status_msg.edit(f"⏳ Rate limit exceeded. Please wait {e.seconds} seconds.")
    except Exception as e:
        logger.error(f"Button {key} error for {uid}: {e}")
        await status_msg.edit("❌ An error occurred. Please try again later.")

# ------------------ GLOBAL DELETE COMMAND ------------------
@client.on(events.NewMessage(pattern=r"^/del$"))
@admin_only
async def global_delete(event):
    if not event.is_reply:
        return await event.reply("❌ `[Error] Please reply to the user's message with /del to wipe it.`")
    
    replied = await event.get_reply_message()
    key = (event.chat_id, replied.id)
    
    if key in admin_msg_map:
        target_msgs = admin_msg_map[key]
        for adm_id, msg_id in target_msgs:
            try:
                await client.delete_messages(adm_id, [msg_id])
            except Exception:
                pass
        
        for adm_id, msg_id in target_msgs:
            admin_msg_map.pop((adm_id, msg_id), None)
            
        del_confirm = await event.respond("✅ `[SYS] Message wiped globally from all Admin panels.`")
        try: await event.delete() 
        except: pass
        await asyncio.sleep(3)
        try: await del_confirm.delete() 
        except: pass
    else:
        warn = await event.reply("⚠️ `[Notice] Cannot wipe. Message not found in active global registry.`")
        await asyncio.sleep(3)
        try: await warn.delete()
        except: pass

# ------------------ ADMIN SMART SEND MESSAGE ------------------
@client.on(events.NewMessage(pattern=r"^✉️ Send Message$"))
@admin_only
async def btn_send_dm_start(event):
    admin_id = event.sender_id
    prompt = await event.reply("👤 **DIRECT VIP MESSAGING**\n➖➖➖➖➖➖➖➖➖➖➖➖\n👉 Niche target user ki **UID** type karke bhejo:\n*(Cancel karne ke liye `/cancel` likhein)*")
    
    admin_chat_state[admin_id] = {
        "step": "waiting_for_id",
        "target_id": None,
        "delete_msgs": [event.id, prompt.id]
    }

@client.on(events.NewMessage(pattern=r"^/cancel$"))
@admin_only
async def cancel_state(event):
    if event.sender_id in admin_chat_state:
        del admin_chat_state[event.sender_id]
        await event.reply("🚫 `[Notice] Operation cancelled successfully.`")

@client.on(events.NewMessage(func=lambda e: e.sender_id in ADMIN_IDS and e.sender_id in admin_chat_state))
async def handle_admin_chat_state(event):
    admin_id = event.sender_id
    state = admin_chat_state[admin_id]
    
    if event.text and event.text.startswith('/'):
        return 
        
    if state["step"] == "waiting_for_id":
        text = event.text.strip()
        if text.lstrip('-').isdigit():
            target_id = int(text)
            state["target_id"] = target_id
            state["step"] = "waiting_for_msg"
            state["delete_msgs"].append(event.id)
            try:
                await client.delete_messages(admin_id, state["delete_msgs"])
            except Exception:
                pass
            prompt = await event.respond(f"✅ **Target UID Locked:** `{target_id}`\n➖➖➖➖➖➖➖➖➖➖➖➖\n✍️ **Ab apna Message, Photo ya Video bhejo:**\n*(Cancel karne ke liye `/cancel` likhein)*")
            state["delete_msgs"] = [prompt.id] 
        else:
            await event.reply("❌ `[Error] Invalid format.` Sirf numbers allow hain. Phir se UID type karein:")
            
    elif state["step"] == "waiting_for_msg":
        target_id = state["target_id"]
        try:
            try:
                await client.delete_messages(admin_id, state["delete_msgs"])
            except Exception:
                pass
            access_hash = tracked_users.get(target_id, 0)
            peer = InputPeerUser(target_id, access_hash) if access_hash else target_id
            
            if event.text:
                await client.send_message(peer, event.text, file=event.media)
            elif event.media:
                await client.send_message(peer, file=event.media)
                
            user_chat_state[target_id] = True 
            await event.respond(f"✅ **VIP Message successfully delivered to `{target_id}`!**")
            del admin_chat_state[admin_id]
        except UserIsBlockedError:
            blocked_users.add(target_id)
            save_user(target_id, tracked_users.get(target_id, 0), blocked=True)
            await event.respond("❌ `[Error] Delivery Failed: User blocked the bot.`")
            del admin_chat_state[admin_id]
        except Exception:
            await event.respond("❌ `[Error] Delivery Failed:` System issue ya invalid ID.")
            del admin_chat_state[admin_id]
            
    raise events.StopPropagation 

@client.on(events.NewMessage(pattern=r"^/send\s+(\d+)(?:\s+(.+))?"))
@admin_only
async def send_dm_manual(event):
    target_uid = int(event.pattern_match.group(1))
    text_msg = event.pattern_match.group(2)
    try:
        peer = InputPeerUser(target_uid, tracked_users.get(target_uid, 0))
        if event.is_reply:
            reply_msg = await event.get_reply_message()
            await client.send_message(peer, text_msg or reply_msg.text or "", file=reply_msg.media)
        elif text_msg:
            await client.send_message(peer, text_msg.strip())
        user_chat_state[target_uid] = True
        await event.reply(f"✅ **Message successfully delivered to `{target_uid}`!**")
    except UserIsBlockedError:
        blocked_users.add(target_uid)
        save_user(target_uid, tracked_users.get(target_uid, 0), blocked=True)
        await event.reply("❌ `[Error] Delivery Failed: User blocked the bot.`")
    except Exception:
        await event.reply("❌ `[Error] Delivery Failed:` System issue ya invalid ID.")

# ------------------ AUTO-RESPONDER COMMANDS ------------------
@client.on(events.NewMessage(pattern=r"^/setreply\s+(.+?)\s*\|\s*(.+)"))
@admin_only
async def set_auto_reply(event):
    keyword = event.pattern_match.group(1).strip().lower()
    response = event.pattern_match.group(2).strip()
    
    auto_replies[keyword] = response
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("INSERT OR REPLACE INTO auto_replies (keyword, response) VALUES (?, ?)", (keyword, response))
        
    await event.reply(f"✅ **Auto-Reply Saved!**\n➖➖➖➖➖➖➖➖➖➖➖➖\n🔑 **Keyword:** `{keyword}`\n🤖 **Response:**\n{response}")

@client.on(events.NewMessage(pattern=r"^/delreply\s+(.+)"))
@admin_only
async def del_auto_reply(event):
    keyword = event.pattern_match.group(1).strip().lower()
    if keyword in auto_replies:
        del auto_replies[keyword]
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM auto_replies WHERE keyword=?", (keyword,))
        await event.reply(f"🗑 **Auto-reply deleted** for keyword: `{keyword}`")
    else:
        await event.reply(f"⚠️ Keyword `{keyword}` database mein nahi mila.")

@client.on(events.NewMessage(pattern=r"^/listreplies$"))
@admin_only
async def list_auto_replies(event):
    if not auto_replies:
        await event.reply("📭 Abhi koi auto-replies set nahi hain.")
        return
        
    msg = "🤖 **ACTIVE AUTO-REPLIES:**\n➖➖➖➖➖➖➖➖➖➖➖➖\n"
    for kw, resp in auto_replies.items():
        short_resp = resp[:25] + "..." if len(resp) > 25 else resp
        msg += f"🔹 `{kw}` ➡ {short_resp}\n"
    await event.reply(msg)

# ------------------ STATS / STATUS / LIVE BROADCAST ------------------
@client.on(events.NewMessage(pattern=r"^(/stats|📊 Stats)$"))
@admin_only
async def stats(event):
    hack_msgs = len(button_forwards.get('hack', []))
    pred_msgs = len(button_forwards.get('prediction', []))
    active_count = len(tracked_users) - len(blocked_users)
    banner_status = "🟢 Set" if banner_config.get("msg_id") else "🔴 None"
    force_status = f"🟢 @{force_channel}" if force_channel else "🔴 OFF"
    
    msg = (
        "📊 **VIP BOT ANALYTICS & METRICS**\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖\n"
        f"⏱ **Server Uptime:** `{get_uptime()}`\n"
        f"👥 **Total Users:** `{len(tracked_users)}` *(🟢 {active_count} Active)*\n"
        f"🚫 **Blocked / Dead:** `{len(blocked_users)}` | **Banned:** `{len(banned_users)}`\n"
        f"👑 **Total Admins:** `{len(ADMIN_IDS)}`\n\n"
        f"📈 **BUTTON CLICK ANALYTICS:**\n"
        f" ├ 📊 Colour Trading: `{click_stats.get('prediction', 0)}` clicks\n"
        f" ├ ⚡ Number Hack: `{click_stats.get('hack', 0)}` clicks\n"
        f" └ 🎧 Admin Support: `{click_stats.get('support', 0)}` clicks\n\n"
        f"⚙️ **SYSTEM CONFIGURATION:**\n"
        f" ├ 🔊 Welcome: `{'🟢 ON' if welcome_enabled else '🔴 OFF'}` ({len(saved_messages)} steps)\n"
        f" ├ 🖼️ Menu Banner: `{banner_status}`\n"
        f" ├ 🔒 Force Join: `{force_status}`\n"
        f" ├ 🎧 Support ID: `{support_username}`\n"
        f" └ 📁 Files: Hack (`{hack_msgs}`) | Prediction (`{pred_msgs}`)\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖"
    )
    await event.reply(msg)

@client.on(events.NewMessage(pattern=r"^(/status|⚙️ Status)$"))
@admin_only
async def status(event):
    steps = "\n".join(f"  Step {s}: {d['type']}" for s, d in sorted(saved_messages.items()))
    await event.reply(
        f"⚙️ **System Diagnostics**\n"
        f"🟢 Bot Username: `@{BOT_USERNAME}`\n"
        f"⏱ Uptime: `{get_uptime()}`\n"
        f"👥 Users: `{len(tracked_users)}`\n"
        f"🔢 Welcome Sequence:\n{steps if steps else '  None configured'}"
    )

@client.on(events.NewMessage(pattern=r"^/broadcast(_pin)?(?:\s+(.+))?"))
@admin_only
async def broadcast(event):
    if not tracked_users:
        return await event.reply("❌ Koi active user nahi hai.")
    
    should_pin = bool(event.pattern_match.group(1))
    text = event.raw_text.replace("/broadcast_pin", "", 1).replace("/broadcast", "", 1).strip()
    
    if not text and not event.is_reply:
        return await event.reply("❌ Message text dein ya kisi message par reply karein.\n💡 *Tip: Use `/broadcast_pin` to auto-pin the message!*")

    total_targets = len(tracked_users)
    status_msg = await event.reply(f"📡 **Initiating VIP Broadcast...**\n👥 Total Targets: `{total_targets}`")
    success = fail = skip = 0
    processed = 0
    reply_msg = await event.get_reply_message() if event.is_reply else None

    for uid, old_hash in list(tracked_users.items()):
        processed += 1
        if uid in blocked_users:
            skip += 1
            continue
        try:
            peer = await client.get_input_entity(uid)
            if reply_msg:
                sent = await client.forward_messages(peer, reply_msg.id, reply_msg.chat_id)
            else:
                sent = await client.send_message(peer, text)
                
            if should_pin and sent:
                try:
                    msg_to_pin = sent[0].id if isinstance(sent, list) else sent.id
                    await client.pin_message(peer, msg_to_pin, notify=True)
                except Exception:
                    pass
                    
            success += 1
            await asyncio.sleep(0.25)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except UserIsBlockedError:
            blocked_users.add(uid)
            save_user(uid, old_hash, blocked=True)
            fail += 1
        except Exception:
            fail += 1

        # Update live progress bar every 25 users
        if processed % 25 == 0:
            pct = int((processed / total_targets) * 100)
            filled = pct // 10
            bar = "█" * filled + "░" * (10 - filled)
            try:
                await status_msg.edit(
                    f"📡 **Broadcasting Live...** `[{bar}] {pct}%`\n"
                    f"➖➖➖➖➖➖➖➖➖➖➖➖\n"
                    f"🔄 Processed: `{processed}/{total_targets}`\n"
                    f"✅ Delivered: `{success}` | ❌ Failed: `{fail}` | ⏭ Skipped: `{skip}`"
                )
            except Exception:
                pass

    await status_msg.edit(
        f"✅ **VIP Broadcast Completed!**\n"
        f"➖➖➖➖➖➖➖➖➖➖➖➖\n"
        f"👥 Total Users: `{total_targets}`\n"
        f"✅ Delivered: `{success}`\n"
        f"❌ Failed/Blocked: `{fail}`\n"
        f"⏭ Skipped: `{skip}`\n"
        f"📌 Auto-Pinned: `{'Yes' if should_pin else 'No'}`"
    )

@client.on(events.NewMessage(pattern=r"^/setmsg(\d+)(?:\s+(.+))?"))
@admin_only
async def setmsg(event):
    step = int(event.pattern_match.group(1))
    extra = event.pattern_match.group(2)
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        data = {"type": "forward", "msg_id": reply_msg.id, "from_chat": event.chat_id}
    elif extra is not None:
        data = {"type": "text", "text": extra.strip()}
    else:
        if step in saved_messages:
            del saved_messages[step]
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.execute("DELETE FROM messages WHERE step=?", (step,))
            return await event.reply(f"🗑 Sequence step {step} hata diya gaya hai.")
        else:
            return await event.reply("❌ Text provide karein ya kisi message par reply karein.")

    saved_messages[step] = data
    save_message_step(step, data)
    await event.reply(f"✅ Sequence Step {step} save ho gaya hai.")

@client.on(events.NewMessage(pattern=r"^/setbutton\s+(hack|prediction)$"))
@admin_only
async def set_button(event):
    btn_key = event.pattern_match.group(1).lower()
    if not event.is_reply: return await event.reply("❌ Please reply to the message you want to set for this button.")
    reply_msg = await event.get_reply_message()
    chat_id = get_chat_id(reply_msg, event)
    delete_button_config(btn_key)
    button_forwards[btn_key] = []
    add_button_config(btn_key, reply_msg.id, chat_id)
    button_forwards[btn_key].append({"msg_id": reply_msg.id, "from_chat": chat_id})
    await event.reply(f"✅ Button **'{btn_key}'** set ho gaya hai! (1 file)\n\n👉 **Tip:** `/addbutton {btn_key}` se aur files add kar sakte hain.")

@client.on(events.NewMessage(pattern=r"^/addbutton\s+(hack|prediction)$"))
@admin_only
async def add_button(event):
    btn_key = event.pattern_match.group(1).lower()
    if not event.is_reply: return await event.reply("❌ Please reply to the message.")
    if btn_key not in button_forwards: button_forwards[btn_key] = []
    reply_msg = await event.get_reply_message()
    chat_id = get_chat_id(reply_msg, event)
    add_button_config(btn_key, reply_msg.id, chat_id)
    button_forwards[btn_key].append({"msg_id": reply_msg.id, "from_chat": chat_id})
    await event.reply(f"✅ Additional file added! Total: `{len(button_forwards[btn_key])}`")

@client.on(events.NewMessage(pattern=r"^/clearbutton\s+(hack|prediction)$"))
@admin_only
async def clear_button(event):
    btn_key = event.pattern_match.group(1).lower()
    if btn_key in button_forwards:
        del button_forwards[btn_key]
        delete_button_config(btn_key)
        await event.reply(f"🗑 Button **'{btn_key}'** clear kar diya gaya hai.")
    else:
        await event.reply("⚠️ Is button ki koi configuration nahi mili.")

def get_chat_id(reply_msg, event):
    if hasattr(reply_msg, "chat_id") and reply_msg.chat_id: return reply_msg.chat_id
    elif hasattr(reply_msg, "peer_id"):
        pid = reply_msg.peer_id
        if isinstance(pid, PeerUser): return pid.user_id
        elif isinstance(pid, PeerChannel): return pid.channel_id
    return event.chat_id

@client.on(events.NewMessage(pattern=r"^(/togglewelcome|🔇 Toggle Welcome)$"))
@admin_only
async def toggle_welcome(event):
    global welcome_enabled
    welcome_enabled = not welcome_enabled
    set_setting("welcome_enabled", "1" if welcome_enabled else "0")
    await event.reply(f"🔊 Welcome protocol is now **{'🟢 ON' if welcome_enabled else '🔴 OFF'}**.")

@client.on(events.NewMessage(pattern=r"^(/backup|📁 Backup)$"))
@admin_only
async def backup(event):
    db_admins = [a for a in ADMIN_IDS if a not in ROOT_ADMINS]
    data = {
        "tracked": tracked_users,
        "blocked": list(blocked_users),
        "banned": list(banned_users),
        "messages": {str(k): v for k, v in saved_messages.items()},
        "button_forwards": button_forwards,
        "welcome": welcome_enabled,
        "inline_text": inline_menu_text,
        "support_username": support_username,
        "force_channel": force_channel,
        "banner_config": banner_config,
        "click_stats": click_stats,
        "auto_replies": auto_replies,
        "db_admins": db_admins
    }
    file = "backup.json"
    with open(file, "w") as f: json.dump(data, f)
    await client.send_file(event.chat_id, file, caption=f"📁 `[VIP System Backup]`\n👥 Users: `{len(tracked_users)}`")
    os.remove(file)

def restore_from_dict(data: dict):
    global tracked_users, blocked_users, banned_users, saved_messages, button_forwards
    global welcome_enabled, inline_menu_text, support_username, force_channel, banner_config, click_stats, auto_replies, ADMIN_IDS
    
    tracked_users = {int(k): int(v) for k, v in data.get("tracked", {}).items()}
    blocked_users = set(int(u) for u in data.get("blocked", []))
    banned_users = set(int(u) for u in data.get("banned", []))
    saved_messages = {int(k): v for k, v in data.get("messages", {}).items()}
    button_forwards = data.get("button_forwards", {})
    welcome_enabled = data.get("welcome", True)
    inline_menu_text = data.get("inline_text", inline_menu_text)
    support_username = data.get("support_username", support_username)
    force_channel = data.get("force_channel", "")
    banner_config = data.get("banner_config", {"msg_id": 0, "chat_id": 0})
    click_stats = data.get("click_stats", {"prediction": 0, "hack": 0, "support": 0})
    auto_replies = data.get("auto_replies", {})
    db_admins = data.get("db_admins", [])

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM button_msgs")
        conn.execute("DELETE FROM auto_replies")
        conn.execute("DELETE FROM admins")
        conn.execute("DELETE FROM banned")
        
        for uid, hsh in tracked_users.items():
            conn.execute("INSERT OR REPLACE INTO users (user_id, access_hash, blocked) VALUES (?,?,?)", (uid, hsh, int(uid in blocked_users)))
        for uid in banned_users:
            conn.execute("INSERT OR IGNORE INTO banned (user_id) VALUES (?)", (uid,))
        for step, d in saved_messages.items():
            conn.execute("INSERT OR REPLACE INTO messages (step, msg_type, text, msg_id, from_chat) VALUES (?,?,?,?,?)", (step, d.get("type"), d.get("text"), d.get("msg_id"), d.get("from_chat")))
        for key, cfg_list in button_forwards.items():
            if isinstance(cfg_list, dict): cfg_list = [cfg_list]
            for cfg in cfg_list: conn.execute("INSERT INTO button_msgs (btn_key, msg_id, from_chat) VALUES (?,?,?)", (key, cfg["msg_id"], cfg["from_chat"]))
        for kw, resp in auto_replies.items():
            conn.execute("INSERT OR REPLACE INTO auto_replies (keyword, response) VALUES (?,?)", (kw, resp))
        for admin_id in db_admins:
            conn.execute("INSERT INTO admins (user_id) VALUES (?)", (admin_id,))
            if admin_id not in ADMIN_IDS:
                ADMIN_IDS.append(admin_id)
            
    set_setting("welcome_enabled", "1" if welcome_enabled else "0")
    set_setting("inline_text", inline_menu_text)
    set_setting("support_username", support_username)
    set_setting("force_channel", force_channel)
    set_setting("banner_config", json.dumps(banner_config))
    set_setting("click_stats", json.dumps(click_stats))
    return len(db_admins)

@client.on(events.NewMessage(pattern=r"^/restore"))
@admin_only
async def restore(event):
    if not event.is_reply: return await event.reply("❌ Please reply to a `.json` backup file.")
    rep = await event.get_reply_message()
    if not rep.file or not rep.file.name.endswith(".json"): return await event.reply("❌ Invalid file format.")
    path = await client.download_media(rep.media)
    try:
        with open(path, "r") as f: data = json.load(f)
        extra_adm = restore_from_dict(data)
        await event.reply(f"✅ **Restore Successful:** Loaded `{len(tracked_users)}` users and `{extra_adm}` extra admins.")
    except Exception as e:
        await event.reply(f"❌ Restore failed: {e}")
    finally:
        if os.path.exists(path): os.remove(path)

@client.on(events.NewMessage(pattern=r"^(📢 Broadcast|🔢 Set Sequence|🔘 Set Button|🗑 Clear Button|👑 Manage Admins|🖼️ Menu & Banner|🔒 Force Join)$"))
@admin_only
async def helper_buttons(event):
    text = event.text
    if text == "📢 Broadcast":
        await event.reply("📢 **Broadcast Guide:**\n👉 Normal: `/broadcast <text>` (or reply to media)\n👉 Auto-Pin: `/broadcast_pin <text>` (pins message in user chat)")
    elif text == "🔢 Set Sequence":
        await event.reply("🔢 **Welcome Sequence Guide:**\n👉 `/setmsg1 <text>` (or reply to media)\n👉 `/setmsg2 <text>`\n👉 Clear step: `/setmsg1` (without text)")
    elif text == "🔘 Set Button":
        await event.reply("🔘 **Set Module Files:**\n👉 `/setbutton hack` (reply to message)\n👉 `/addbutton hack` (stack multiple files)\n👉 Same for `prediction`.")
    elif text == "🗑 Clear Button":
        await event.reply("🗑 **Clear Module Files:**\n👉 `/clearbutton hack`\n👉 `/clearbutton prediction`")
    elif text == "🖼️ Menu & Banner":
        await event.reply(
            "🖼️ **MENU & BANNER CUSTOMIZATION**\n"
            "➖➖➖➖➖➖➖➖➖➖➖➖\n"
            "👉 **Set Photo/Video Banner:** Reply to any Photo/Video/GIF with `/setbanner`\n"
            "👉 **Remove Banner:** `/clearbanner`\n"
            "👉 **Set Menu Text:** `/setinline <text>` (or reply to text)\n"
            "👉 **Change Support ID:** `/setsupport @username`"
        )
    elif text == "🔒 Force Join":
        await event.reply(
            "🔒 **FORCE SUBSCRIBE CHANNEL SETUP**\n"
            "➖➖➖➖➖➖➖➖➖➖➖➖\n"
            f"📌 **Current Status:** `{('@' + force_channel) if force_channel else 'Disabled'}`\n\n"
            "👉 **Enable Force Join:** `/setforce @ChannelUsername`\n"
            "👉 **Disable Force Join:** `/clearforce`\n\n"
            "⚠️ *Important: Add this bot as an Admin in your channel first!*"
        )
    elif text == "👑 Manage Admins":
        help_text = (
            "👑 **ADMIN & SECURITY PANEL**\n"
            "➖➖➖➖➖➖➖➖➖➖➖➖\n"
            "👉 **Add Admin:** `/addadmin <user_id>`\n"
            "👉 **Remove Admin:** `/deladmin <user_id>`\n"
            "👉 **List Admins:** `/listadmins`\n"
            "👉 **Ban Spammer:** `/ban <user_id>`\n"
            "👉 **Unban User:** `/unban <user_id>`"
        )
        await event.reply(help_text)

@client.on(events.NewMessage(pattern=r"^(📁 Backup|🔄 Restore)$"))
@admin_only
async def backup_restore_buttons(event):
    if event.text == "📁 Backup":
        await backup(event)
    elif event.text == "🔄 Restore":
        if os.path.exists("auto_backup.json"):
            try:
                with open("auto_backup.json", "r") as f: data = json.load(f)
                restore_from_dict(data)
                await event.reply(f"✅ Restored from auto-backup successfully. Active users: `{len(tracked_users)}`")
            except Exception as e:
                await event.reply(f"❌ Auto-restore failed: {e}")
        else:
            await event.reply("🔄 **Restore Guide:**\n1. Generate a backup using `/backup`.\n2. Reply to that `.json` file with `/restore`.")

@client.on(events.NewMessage(pattern=r"^(/cleanup|🧹 Cleanup)$"))
@admin_only
async def cleanup(event):
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("DELETE FROM users WHERE blocked=1")
    removed = len(blocked_users)
    for uid in list(blocked_users):
        tracked_users.pop(uid, None)
    blocked_users.clear()
    await event.reply(f"🧹 **Database Cleaned!** Removed `{removed}` dead/blocked accounts.")

# ------------------ TWO-WAY CHAT & AUTO-REPLY ------------------
@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def seamless_chat_handler(event):
    if event.sender_id in banned_users or event.sender_id in admin_chat_state:
        return

    text = event.raw_text.lower() if event.raw_text else ""
    admin_btns = {
        "✉️ send message", "📢 broadcast", "📊 stats", "⚙️ status",
        "🔢 set sequence", "🔇 toggle welcome", "📁 backup", "🔄 restore",
        "🔘 set button", "🗑 clear button", "🧹 cleanup", "👑 manage admins",
        "🖼️ menu & banner", "🔒 force join"
    }
    if text.startswith('/') or text in admin_btns:
        return

    if event.sender_id not in ADMIN_IDS:
        if user_chat_state.get(event.sender_id):
            bot_auto_replied = False
            for kw, resp in auto_replies.items():
                if kw in text:
                    try:
                        await client.send_message(event.sender_id, resp)
                        bot_auto_replied = True
                        break 
                    except Exception:
                        pass

            user = await event.get_sender()
            name = user.first_name or "User"
            auto_tag = "🤖 *(AI Auto-Responded)*\n" if bot_auto_replied else ""
            
            caption = (
                f"💬 **SUPPORT INTERCEPT** 💬\n"
                f"➖➖➖➖➖➖➖➖➖➖➖➖\n"
                f"{auto_tag}"
                f"👤 **From:** [{name}](tg://user?id={event.sender_id})\n"
                f"🆔 `{event.sender_id}`\n"
            )
            
            if event.raw_text: caption += f"\n📝 **Message:**\n{event.raw_text}"
            caption += "\n\n👇 *(Reply to chat, `/del` to wipe, or `/ban ID`)*"
            
            sent_admin_msgs = []
            for admin_id in ADMIN_IDS:
                try: 
                    sent_msg = await client.send_message(admin_id, caption, file=event.media)
                    sent_admin_msgs.append((admin_id, sent_msg.id))
                except Exception: 
                    pass
            
            if sent_admin_msgs:
                for adm_id, msg_id in sent_admin_msgs:
                    admin_msg_map[(adm_id, msg_id)] = sent_admin_msgs
                    
            try:
                if not bot_auto_replied:
                    feedback_msg = await event.reply("✅ *Message delivered to VIP Admin.*")
                    await asyncio.sleep(3)
                    await feedback_msg.delete()
            except Exception:
                pass
                
        else:
            try: await event.delete()
            except Exception: pass
            warning_msg = await event.respond("⚠️ **Direct messages are disabled.**\n👉 Please click **'🎧 ADMIN SUPPORT'** in the menu to chat with an admin.")
            await asyncio.sleep(5)
            try: await warning_msg.delete()
            except Exception: pass
    else:
        if event.is_reply:
            replied_msg = await event.get_reply_message()
            replied_text = replied_msg.raw_text or ""
            
            match = re.search(r"🆔 `(\d+)`", replied_text)
            if match:
                target_uid = int(match.group(1))
                try:
                    access_hash = tracked_users.get(target_uid, 0)
                    peer = InputPeerUser(target_uid, access_hash) if access_hash else target_uid 
                        
                    if event.raw_text: await client.send_message(peer, event.raw_text, file=event.media)
                    elif event.media: await client.send_message(peer, file=event.media)
                        
                    user_chat_state[target_uid] = True
                    admin_feedback = await event.reply("✅ **Sent securely!**")
                    await asyncio.sleep(2)
                    try: await admin_feedback.delete()
                    except Exception: pass
                        
                except UserIsBlockedError:
                    blocked_users.add(target_uid)
                    save_user(target_uid, tracked_users.get(target_uid, 0), blocked=True)
                    await event.reply("❌ `[Error] Transmission failed: User blocked the bot.`")
                except Exception as e:
                    await event.reply(f"❌ **Error:** {e}")

# ------------------ AUTO BACKUP (EVERY 6 HOURS) ------------------
async def periodic_backup():
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            db_admins = [a for a in ADMIN_IDS if a not in ROOT_ADMINS]
            data = {
                "tracked": tracked_users,
                "blocked": list(blocked_users),
                "banned": list(banned_users),
                "messages": {str(k): v for k, v in saved_messages.items()},
                "button_forwards": button_forwards,
                "welcome": welcome_enabled,
                "inline_text": inline_menu_text,
                "support_username": support_username,
                "force_channel": force_channel,
                "banner_config": banner_config,
                "click_stats": click_stats,
                "auto_replies": auto_replies,
                "db_admins": db_admins
            }
            file = "auto_backup.json"
            with open(file, "w") as f:
                json.dump(data, f)
            
            caption = f"📁 `[Automatic 6-Hour VIP Backup]`\n📊 Users: `{len(tracked_users)}` | Uptime: `{get_uptime()}`"
            for admin_id in ROOT_ADMINS:
                try:
                    await client.send_file(admin_id, file, caption=caption)
                except Exception as e:
                    logger.error(f"Failed to send periodic backup to admin {admin_id}: {e}")
        except Exception as e:
            logger.error(f"Periodic backup error: {e}")

# ------------------ MAIN ------------------
async def main():
    global BOT_USERNAME
    await client.start(bot_token=BOT_TOKEN)
    try:
        me = await client.get_me()
        if me and me.username:
            BOT_USERNAME = me.username
    except Exception:
        pass
    init_db()
    load_from_db()
    await start_web()
    asyncio.create_task(periodic_backup())
    logger.info(f"ProBot VIP (@{BOT_USERNAME}) is running smoothly...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    try: client.loop.run_until_complete(main())
    except KeyboardInterrupt: logger.info("Bot stopped.")
    except Exception as e: logger.error(f"Fatal error: {e}")
