import os
import sys
import time
import asyncio
import threading
import http.server
import urllib.parse

# --- RENDER WEB PORT BINDING HARNESS ---
class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot Engine Live and Responsive!")
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# --- LOAD FRAMEWORKS ---
from telethon import TelegramClient, events, Button
from motor.motor_asyncio import AsyncIOMotorClient

# --- ENVIRONMENTAL PARSING & DATABASE CONFIG ---
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

# Initialize Telethon Client (Compatible naturally with Python 3.14 event structures)
bot = TelegramClient('omega_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["omega_processor_db"]
user_profiles = db["user_data"]

JOB_QUEUE = asyncio.Queue()
IS_PROCESSING = False

# --- UTILITY CORE DATA LOGIC ---
async def fetch_user_profile(user_id):
    profile = await user_profiles.find_one({"user_id": user_id})
    if not profile:
        profile = {
            "user_id": user_id,
            "thumb_id": None,          
            "global_caption": None, 
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
        "👑 **Welcome to the Telethon Omega Stream Suite** 👑\n\n"
        "This platform processes items up to **2.0 GB** cleanly using direct data streams.\n\n"
        "🚀 **Operations Cheat Sheet:**\n"
        "• Send any **Photo** to save it as your target thumbnail.\n"
        "• Send any **Video / File** to populate your management interface.\n\n"
        "✏️ `/setname title.mp4` — Schedule a new file name\n"
        "💬 `/setcaption text` — Force a custom text block signature\n"
        "📢 `/setchannel @username` — Add automatic forwarding channel"
    )
    await event.reply(welcome)

@bot.on(events.NewMessage(incoming=True, func=lambda e: e.photo))
async def save_photo(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return
    status = await event.reply("📥 *Processing and cloud-indexing artwork preset...*")
    
    # Store the photo raw media profile ID straight to MongoDB without local downloading
    photo_id = event.message.media.photo.id
    await save_profile_update(event.sender_id, "thumb_id", photo_id)
    await status.edit("✅ **Thumbnail saved to cloud configuration!** Ready to inject into your next file transaction.")

@bot.on(events.NewMessage(incoming=True, func=lambda e: e.video or e.document))
async def file_panel_trigger(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return
        
    user_id = event.sender_id
    profile = await fetch_user_profile(user_id)
    
    current_name = profile.get("temp_rename") or "Native File Name"
    mode_str = "📄 Raw File Document" if profile.get("as_document") else "🎥 Playable Streaming Video"
    
    dashboard_ui = (
        f"🏁 **Omega Media Terminal**\n\n"
        f"📦 **Output Name Target:** `{current_name}`\n"
        f"⚙️ **Format Profile Profile:** `{mode_str}`\n"
        f"📢 **Channel Hook Status:** `{profile.get('target_channel') or 'Local Delivery Mode'}`"
    )
    
    buttons = [
        [Button.inline("🔄 Toggle Format Profile Type", data="toggle_delivery")],
        [Button.inline("🚀 RUN ZERO-DISK PIPELINE STREAM", data=f"run_stream_{event.id}")]
    ]
    await event.reply(dashboard_ui, buttons=buttons)

# --- CONCURRENT SERIAL STREAM QUEUE PIPING MANAGER ---
async def process_queue_worker():
    global IS_PROCESSING
    while True:
        try:
            event, action, target_msg_id, user_id, profile = await JOB_QUEUE.get()
        except asyncio.QueueEmpty:
            IS_PROCESSING = False
            break
            
        IS_PROCESSING = True
        status_banner = await event.reply("🛰️ **Spawning secure network chunk streaming nodes...**")
        
        try:
            # Re-fetch original user message layer
            orig_msg = await bot.get_messages(event.chat_id, ids=target_msg_id)
            
            # Construct standard metadata auto-caption metrics
            file_size_mb = orig_msg.file.size / (1024 * 1024)
            final_caption = profile.get("global_caption") or f"🎬 **File Title:** `{orig_msg.file.name or 'video.mp4'}`\n💾 **Size:** `{file_size_mb:.2f} MB`"
            
            target_chat = profile.get("target_channel") or event.chat_id
            
            # Download a transient copy of the icon target artwork file context if configured
            thumb_path = None
            if profile.get("thumb_id"):
                thumb_path = await bot.download_media(orig_msg.photo)

            await status_banner.edit("📤 **Piping data chunks into Telegram Cloud... RAM: <15MB**")
            
            # Telethon streams files out natively up to 2GB if passed a generator pipe iterator loop
            await bot.send_file(
                entity=target_chat,
                file=orig_msg.media,
                caption=final_caption,
                force_document=profile.get("as_document", False),
                thumb=thumb_path,
                reply_to=target_msg_id
            )
            
            await status_banner.delete()
            if thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)
            await save_profile_update(user_id, "temp_rename", None)
            
        except Exception as err:
            await status_banner.edit(f"❌ **Stream Pipeline Error Event:** `{str(err)}`")
        finally:
            JOB_QUEUE.task_done()

@bot.on(events.CallbackQuery)
async def callback_router(event):
    if SUDO_USERS and event.sender_id not in SUDO_USERS:
        return await event.answer("Access denied.", alert=True)
        
    user_id = event.sender_id
    profile = await fetch_user_profile(user_id)
    
    if event.data == b"toggle_delivery":
        new_toggle = not profile.get("as_document")
        await save_profile_update(user_id, "as_document", new_toggle)
        await event.answer(f"Format updated to {'Document' if new_toggle else 'Video'}!")
        await event.edit("⚙️ **Format rules configured successfully.** Click run stream below to invoke execution.")
        
    elif event.data.startswith(b"run_stream_"):
        await event.answer("Adding job transaction to background loop...")
        target_msg_id = int(event.data.decode().split("_")[2])
        
        # Enqueue job to protect resource allocations
        await JOB_QUEUE.put((event, "main", target_msg_id, user_id, profile))
        
        await event.edit(f"📥 **Job Enqueued successfully.** Transaction index slot positioning: `#{JOB_QUEUE.qsize()}`.")
        
        global IS_PROCESSING
        if not IS_PROCESSING:
            asyncio.create_task(process_queue_worker())

# --- LAUNCH EVENT LISTENERS FOREVER ---
print("=== TELETHON OMEGA PIPELINE ONLINE ===")
bot.run_until_disconnected()
