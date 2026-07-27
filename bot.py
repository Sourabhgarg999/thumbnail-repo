import os
import sys
import time
import asyncio
import threading
import http.server
import urllib.parse

# --- CRITICAL PYTHON 3.14+ ASYNCIO EVENT LOOP FIX ---
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# Safe to import external frameworks now
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram.errors import FloodWait

# --- RENDER PORT BINDING SYSTEM ---
class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot Engine Live and Responsive!")

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# --- INFRASTRUCTURE CONFIGURATIONS & URI AUTO-ESCAPE ---
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

bot = Client("omega_media_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["omega_processor_db"]
user_profiles = db["user_data"]

JOB_QUEUE = asyncio.Queue()
IS_PROCESSING = False

# --- SYSTEM PERMISSION UTILITIES ---
def is_authorized_user():
    async def func(flt, client, message: Message):
        if not SUDO_USERS:
            return True
        return message.from_user.id in SUDO_USERS
    return filters.create(func)

async def fetch_user_profile(user_id):
    profile = await user_profiles.find_one({"user_id": user_id})
    if not profile:
        profile = {
            "user_id": user_id,
            "presets": {},          
            "active_preset": None,
            "global_caption": None, 
            "as_document": False,
            "target_channel": None,
            "temp_rename": None
        }
        await user_profiles.insert_one(profile)
    return profile

async def save_profile_update(user_id, system_key, value_payload):
    await user_profiles.update_one({"user_id": user_id}, {"$set": {system_key: value_payload}}, upsert=True)

# --- SPEED COMPOSER INTERFACE ---
async def progress_bar_handler(current, total, status_message, action_text, start_time):
    now = time.time()
    diff = now - start_time
    if round(diff % 4.0) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff if diff > 0 else 0
        time_elapsed = round(diff)
        eta = round((total - current) / speed) if speed > 0 else 0
        
        completed_blocks = int(percentage // 10)
        remaining_blocks = 10 - completed_blocks
        bar_visual = "🟩" * completed_blocks + "⬜" * remaining_blocks
        
        progress_text = (
            f"⚡ **{action_text}**\n\n"
            f"📊 {bar_visual} | **{percentage:.1f}%**\n"
            f"⚙️ **Processed:** {current / (1024*1024):.1f} MB / {total / (1024*1024):.1f} MB\n"
            f"🚀 **Speed:** {speed / (1024*1024):.2f} MB/s\n"
            f"⏳ **ETA:** {eta}s | **Elapsed:** {time_elapsed}s\n"
            f"⚠️ *Streaming live without filling up server disk storage.*"
        )
        try:
            await status_message.edit_text(progress_text)
        except FloodWait as fw_err:
            await asyncio.sleep(fw_err.value)
        except Exception:
            pass

def generate_metadata_caption(media, custom_caption=None):
    if custom_caption:
        return custom_caption
        
    file_name = media.file_name or "video.mp4"
    file_size = f"{media.file_size / (1024*1024):.2f} MB"
    
    duration_raw = getattr(media, "duration", 0) or 0
    if duration_raw:
        hours = duration_raw // 3600
        minutes = (duration_raw % 3600) // 60
        seconds = duration_raw % 60
        duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
    else:
        duration_str = "Unknown"
        
    width = getattr(media, "width", "N/A")
    height = getattr(media, "height", "N/A")
    resolution = f"{width}x{height}" if width != "N/A" else "Standard Document"

    return (
        f"🎬 **File Name:** `{file_name}`\n"
        f"💾 **Size:** `{file_size}`\n"
        f"⏱️ **Duration:** `{duration_str}`\n"
        f"📺 **Resolution:** `{resolution}`\n\n"
        f"⚡ *Auto-Generated by Omega Stream Suite*"
    )

# --- BOT EVENTS MODULES ---

@bot.on_message(filters.command("start") & filters.private & is_authorized_user())
async def start_command(client: Client, message: Message):
    welcome = (
        "👑 **Welcome to the Omega Media Stream Suite** 👑\n\n"
        "This system processes files up to **2.0 GB** sequentially with **zero storage usage**.\n\n"
        "🚀 **Quick Operations Reference:**\n"
        "• Send a **Photo** to save / index a new thumbnail preset.\n"
        "• Send a **Video / File** to launch the processing interface panel.\n\n"
        "✏️ `/setname title.mp4` — Override target file title\n"
        "💬 `/setcaption text` — Force custom caption string\n"
        "📢 `/setchannel @username` — Direct distribution automation channel\n"
        "🗑️ `/clearcaption` — Switch back to auto-captioning metadata mode"
    )
    btn1 = InlineKeyboardButton("📖 Read System Manual", callback_data="open_help")
    btn2 = InlineKeyboardButton("⚙️ System Status Profile", callback_data="show_status")
    menu_buttons = InlineKeyboardMarkup([[btn1], [btn2]])
    await message.reply_text(welcome, reply_markup=menu_buttons)

@bot.on_message(filters.command("help") & filters.private & is_authorized_user())
async def help_command(client: Client, message: Message):
    guide = (
        "⚙️ **System Operations Reference:**\n\n"
        "🧱 **Batch Queue:** Handles uploads sequentially to protect Render's free RAM.\n\n"
        "📂 **Preset Slots:** Switch between multiple thumbnail slots on the fly.\n\n"
        "🎵 **Extract MP3:** Isolates and extracts the clean audio track from the media stream."
    )
    await message.reply_text(guide)

@bot.on_message(filters.command("setcaption") & filters.private & is_authorized_user())
async def set_caption(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("❌ **Usage:** `/setcaption Write your text here`")
    caption_txt = message.text.split(None, 1)
    await save_profile_update(message.from_user.id, "global_caption", caption_txt)
    await message.reply_text("✅ **Custom caption template locked successfully.**")

@bot.on_message(filters.command("clearcaption") & filters.private & is_authorized_user())
async def clear_caption(client: Client, message: Message):
    await save_profile_update(message.from_user.id, "global_caption", None)
    await message.reply_text("✅ **Custom template cleared. Automated metadata active.**")

@bot.on_message(filters.command("setname") & filters.private & is_authorized_user())
async def set_name(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("❌ **Usage:** `/setname filename.mp4` (Include extension!)")
    target_name = message.text.split(None, 1)
    await save_profile_update(message.from_user.id, "temp_rename", target_name)
    await message.reply_text(f"✏️ **Next queued transaction file title targeted as:** `{target_name}`")

@bot.on_message(filters.command("setchannel") & filters.private & is_authorized_user())
async def set_channel(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("❌ **Usage:** `/setchannel @mychannelusername`")
    channel_target = message.text.split(None, 1)
    await save_profile_update(message.from_user.id, "target_channel", channel_target)
    await message.reply_text(f"📢 **Distribution Target set to:** `{channel_target}`")

@bot.on_message(filters.photo & filters.private & is_authorized_user())
async def process_photo_preset(client: Client, message: Message):
    file_id = message.photo.file_id
    user_id = message.from_user.id
    preset_key = f"Slot_{int(time.time()) % 1000}"
    profile = await fetch_user_profile(user_id)
    
    current_presets = profile.get("presets", {})
    current_presets[preset_key] = file_id
    
    await save_profile_update(user_id, "presets", current_presets)
    await save_profile_update(user_id, "active_preset", preset_key)
    await message.reply_text(f"✅ **Artwork saved permanently! Slot ID:** `{preset_key}`")

@bot.on_message((filters.video | filters.document) & filters.private & is_authorized_user())
async def trigger_dashboard(client: Client, message: Message):
    user_id = message.from_user.id
    profile = await fetch_user_profile(user_id)
    media = message.video or message.document
    chosen_name = profile.get("temp_rename") or media.file_name or "video.mp4"
    format_type = "📄 Raw File Document" if profile.get("as_document") else "🎥 Playable Media Container"
    active_thumb = profile.get("active_preset") or "None ❌ (Uses Native Frame)"
    channel_hook = profile.get("target_channel") or "Local Delivery Mode"
    
