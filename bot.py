import os
import sys
import time
import asyncio
import threading
import http.server
import urllib.parse

# --- RENDER WEB PORT BINDING ALIVE SERVER ---
class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Omega Advanced Telethon Engine Live!")
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# --- LOAD ADVANCED LIBRARIES ---
from telethon import TelegramClient, events, Button
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo, DocumentAttributeAudio
from motor.motor_asyncio import AsyncIOMotorClient

# --- CONFIGURATIONS & CREDENTIALS AUTO-ESCAPE ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")

if MONGO_URI and "@" in MONGO_URI and ":" in MONGO_URI:
    try:
        prefix, remainder = MONGO_URI.split("://", 1)
        userinfo, hostinfo = remainder.rsplit("@", 1)
        username, password = userinfo.split(":", 1)
        safe_user = urllib.parse.quote_plus(username)
        safe_pass = urllib.parse.quote_plus(password)
        MONGO_URI = f"{prefix}://{safe_user}:{safe_pass}@{hostinfo}"
    except Exception:
        pass 

SUDO_USERS = [int(x.strip()) for x in os.environ.get("SUDO_USERS", "").split(",") if x.strip()]

# Initialize Unified Telethon Client
bot = TelegramClient('omega_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["omega_processor_db"]
user_profiles = db["user_data"]

JOB_QUEUE = asyncio.Queue()
IS_PROCESSING = False

# --- MONGO CLUSTER STATE DATABASE MANAGERS ---
async def fetch_user_profile(user_id):
    profile = await user_profiles.find_one({"user_id": user_id})
    if not profile:
        profile = {
            "user_id": user_id,
            "presets": {},            # Format: {"Slot_1": {"thumb": media_id, "caption": text, "watermark": text}}
            "active_preset": None,
            "as_document": False,
            "target_channel": None,
            "temp_rename": None
        }
        await user_profiles.insert_one(profile)
    return profile

async def save_profile_update(user_id, system_key, value_payload):
    await user_profiles.update_one({"user_id": user_id}, {"$set": {system_key: value_payload}}, upsert=True)

# --- TELEGRAM INBOUND TRIGGER INTERCEPTORS ---

@bot.on(events.NewMessage(pattern='/start', incoming=True))
async def start_cmd(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return
    welcome = (
        "👑 **Welcome to the Omega Ultimate Media Suite** 👑\n\n"
        "I am fully optimized to process files up to **2.0 GB** cleanly using streaming chunks and zero server disk space.\n\n"
        "🚀 **Quick Operations Reference:**\n"
        "• Send any **Photo** to save a thumbnail profile to your current active preset.\n"
        "• Send any **Video / File** to launch the processing controller screen.\n\n"
        "✏️ `/setname title.mp4` — Rename the next file transaction\n"
        "💬 `/setcaption text` — Save a custom text signature stamp to the active preset\n"
        "🏷️ `/setwatermark text` — Save a text title metadata watermark to the active preset\n"
        "📂 `/newpreset name` — Create a new multi-preset profile slot\n"
        "📢 `/setchannel @username` — Route uploads directly to your channel grid\n"
        "ℹ️ `/help` — Read the core technical operations manual"
    )
    buttons = [
        [Button.inline("📖 Read System Manual", data="ui_manual_help")],
        [Button.inline("⚙️ Check System Status Profile", data="ui_view_status")]
    ]
    await event.reply(welcome, buttons=buttons)

@bot.on(events.NewMessage(pattern='/help', incoming=True))
async def help_cmd(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return
    guide = (
        "⚙️ **System Operations Reference Help:**\n\n"
        "🧱 **Sequential Batch Queue:** If you submit multiple massive files simultaneously, the bot puts them into an async background loop to process them one-by-one so your free server never crashes.\n\n"
        "🏷️ **Text Title Watermarking:** Use `/setwatermark text` to permanently write text into the video's property headers. When a user streams the file or forwards it, your text shows inside the player envelope natively.\n\n"
        "💬 **Automatic Text Signature Stamp:** Use `/setcaption text` to add a persistent signature or link under all files processed by the current preset.\n\n"
        "📂 **Multi-Preset Manager:** Use `/newpreset name` to build custom setting configurations. Use 'Cycle Presets' on the dashboard to swap between setups."
    )
    await event.reply(guide)

@bot.on(events.NewMessage(pattern='/newpreset(?: |$)(.*)', incoming=True))
async def new_preset_cmd(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return
    preset_name = event.pattern_match.group(1).strip()
    if not preset_name:
        return await event.reply("❌ **Usage:** `/newpreset ProfileName` (e.g., `/newpreset AnimeChannel`)")
    
    profile = await fetch_user_profile(event.sender_id)
    presets = profile.get("presets", {})
    
    if preset_name in presets:
        return await event.reply("❌ That preset name already exists.")
        
    presets[preset_name] = {"thumb": None, "caption": None, "watermark": "Omega Suite"}
    await save_profile_update(event.sender_id, "presets", presets)
    await save_profile_update(event.sender_id, "active_preset", preset_name)
    await event.reply(f"✅ **Created and switched to new preset slot:** `{preset_name}`")

@bot.on(events.NewMessage(pattern='/setcaption(?: |$)(.*)', incoming=True))
async def set_caption_cmd(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return
    caption_text = event.pattern_match.group(1).strip()
    profile = await fetch_user_profile(event.sender_id)
    active_key = profile.get("active_preset")
    
    if not active_key:
        return await event.reply("❌ Please create or select a preset slot first using `/newpreset`.")
        
    if not caption_text:
        return await event.reply("❌ **Usage:** `/setcaption Write your text signature here`")
        
    presets = profile.get("presets", {})
    presets[active_key]["caption"] = caption_text
    await save_profile_update(event.sender_id, "presets", presets)
    await event.reply(f"✅ **Custom signature stamp saved to preset** `{active_key}`!")

@bot.on(events.NewMessage(pattern='/setwatermark(?: |$)(.*)', incoming=True))
async def set_watermark_cmd(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return
    watermark_text = event.pattern_match.group(1).strip()
    profile = await fetch_user_profile(event.sender_id)
    active_key = profile.get("active_preset")
    
    if not active_key:
        return await event.reply("❌ Please create or select a preset slot first using `/newpreset`.")
        
    if not watermark_text:
        return await event.reply("❌ **Usage:** `/setwatermark YourBrandText`")
        
    presets = profile.get("presets", {})
    presets[active_key]["watermark"] = watermark_text
    await save_profile_update(event.sender_id, "presets", presets)
    await event.reply(f"✅ **Text envelope watermark saved to preset** `{active_key}`!")

@bot.on(events.NewMessage(pattern='/setname(?: |$)(.*)', incoming=True))
async def set_name_cmd(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return
    name_text = event.pattern_match.group(1).strip()
    if not name_text:
        return await event.reply("❌ **Usage:** `/setname movie_filename.mp4` *(Include the extension!)*")
    await save_profile_update(event.sender_id, "temp_rename", name_text)
    await event.reply(f"✏️ **Next file processing title scheduled as:** `{name_text}`")

@bot.on(events.NewMessage(pattern='/setchannel(?: |$)(.*)', incoming=True))
async def set_channel_cmd(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return
    channel_text = event.pattern_match.group(1).strip()
    if not channel_text:
        return await event.reply("❌ **Usage:** `/setchannel @MyChannelUsername` or numeric chat ID")
    await save_profile_update(event.sender_id, "target_channel", channel_text)
    await event.reply(f"📢 **Automated Distribution Target Channel set to:** `{channel_text}`")

# --- PHOTO IMAGE INPUT PRESET SLOTS ---
@bot.on(events.NewMessage(incoming=True, func=lambda e: e.photo))
async def photo_handler(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return
    
    profile = await fetch_user_profile(event.sender_id)
    active_key = profile.get("active_preset")
    
    if not active_key:
        # Create a default slot if none exists
        active_key = "Default"
        profile["presets"]["Default"] = {"thumb": None, "caption": None, "watermark": "Omega Suite"}
        await save_profile_update(event.sender_id, "active_preset", "Default")

    status = await event.reply("📥 *Linking and database-indexing your thumbnail artwork...*")
    
    presets = profile.get("presets", {})
    presets[active_key]["thumb"] = event.message.media
    
    await save_profile_update(event.sender_id, "presets", presets)
    await status.edit(f"✅ **Thumbnail Artwork indexed directly under preset profile:** `{active_key}`!")

# --- DYNAMIC INTERACTIVE CORE DASHBOARD TRIGGER ---
@bot.on(events.NewMessage(incoming=True, func=lambda e: e.video or e.document))
