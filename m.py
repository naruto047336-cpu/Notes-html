import os
import asyncio
import base64
import uuid
import time
import re
from threading import Thread
from flask import Flask
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BotCommand
from pyrogram.errors import UserNotParticipant
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DB_URL")
ADMINS = [int(id) for id in os.getenv("ADMINS", "").split()]
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL"))
PORT = int(os.getenv("PORT", 8080))
AUTO_DELETE_TIME = 86400 

# --- DATABASE SETUP ---
db_client = AsyncIOMotorClient(DB_URL)
db = db_client["Final_Ultra_V21_ProMax"]
users_db = db.users
batches_db = db.batches
settings_db = db.settings
channels_db = db.fs_channels

app = Client("pro_file_store", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
web_app = Flask(__name__)

# --- TRACKING ---
user_data = {}        
waiting_tasks = {}    
last_ui_msg = {}      
admin_state = {}
# New Tracking for Post Maker
post_maker_data = {}      

# --- HELPERS ---
def encode(text):
    return base64.urlsafe_b64encode(str(text).encode('ascii')).decode('ascii').strip("=")

def decode(b64): 
    padding = '=' * (4 - len(b64) % 4)
    return base64.urlsafe_b64decode((b64 + padding).encode('ascii')).decode('ascii')

async def send_log(text):
    try:
        await app.send_message(LOG_CHANNEL, text)
    except:
        pass

def clean_caption(text):
    if not text:
        return ""
    text = re.sub(r'@[A-Za-z0-9_]+', '', text) 
    text = re.sub(r'https?://t\.me/[A-Za-z0-9_/]+', '', text) 
    return re.sub(r'\n\s*\n', '\n', text).strip()

async def get_config(config_id):
    data = await settings_db.find_one({"id": config_id})
    return data if data else None

async def check_all_subs(user_id):
    not_joined = []
    async for ch in channels_db.find({}):
        try:
            member = await app.get_chat_member(ch["_id"], user_id)
            if member.status in ["kicked", "left"]:
                not_joined.append(ch)
        except UserNotParticipant:
            not_joined.append(ch)
        except:
            continue
    return not_joined

# --- WEB SERVER ---
@web_app.route('/')
def home():
    return "Bot is Online & Multi-Media Supported!", 200

def run_flask():
    web_app.run(host='0.0.0.0', port=PORT)

# ==========================================
#  🆕 POST MAKER FEATURE (Integrated)
# ==========================================

def get_post_editor_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Set Details", callback_data="pm_set_details"),
            InlineKeyboardButton("🔗 Set Link", callback_data="pm_set_link")
        ],
        [InlineKeyboardButton("✅ GET RESULT", callback_data="pm_generate")],
        [InlineKeyboardButton("❌ Cancel", callback_data="pm_cancel")]
    ])

@app.on_message(filters.command("edit_link") & filters.private)
async def pm_start_command(client, message):
    user_id = message.from_user.id
    # Reset any existing file store session to avoid conflict
    user_data.pop(user_id, None) 
    
    post_maker_data[user_id] = {"step": "waiting_for_photo", "photo": None, "caption": None, "link": None}
    await message.reply_text("📸 **New Post Mode**\n\nKripya wo **PHOTO** bhejein jo aap use karna chahte hain.")

# Group -1 ensures this runs BEFORE the File Store Collector
@app.on_message(filters.photo & filters.private, group=-1)
async def pm_photo_handler(client, message):
    user_id = message.from_user.id
    
    if user_id in post_maker_data and post_maker_data[user_id]["step"] == "waiting_for_photo":
        post_maker_data[user_id]["photo"] = message.photo.file_id
        post_maker_data[user_id]["step"] = "menu_mode"
        
        await message.reply_photo(
            photo=message.photo.file_id,
            caption="🖼 **Photo Selected!**\n\nAb neeche diye gaye buttons se Link aur Details add karein.",
            reply_markup=get_post_editor_keyboard()
        )
        # Stop propagation prevents the 'store' logic from running
        message.stop_propagation()

@app.on_message(filters.text & filters.private, group=-1)
async def pm_text_handler(client, message):
    user_id = message.from_user.id
    
    if user_id in post_maker_data:
        step = post_maker_data[user_id]["step"]

        if step == "waiting_for_caption":
            post_maker_data[user_id]["caption"] = message.text
            post_maker_data[user_id]["step"] = "menu_mode"
            
            lnk = post_maker_data[user_id].get("link", "Not Set")
            await message.reply_photo(
                photo=post_maker_data[user_id]["photo"],
                caption=f"✅ **Saved!**\n\n📝 **Details:** {message.text}\n🔗 **Link:** {lnk}",
                reply_markup=get_post_editor_keyboard()
            )
            message.stop_propagation()

        elif step == "waiting_for_link":
            if "http" not in message.text:
                await message.reply_text("⚠️ Invalid Link! http/https jaruri hai.")
                message.stop_propagation()
                return
            
            post_maker_data[user_id]["link"] = message.text
            post_maker_data[user_id]["step"] = "menu_mode"
            
            cap = post_maker_data[user_id].get("caption", "No details.")
            await message.reply_photo(
                photo=post_maker_data[user_id]["photo"],
                caption=f"✅ **Saved!**\n\n📝 **Details:** {cap}\n🔗 **Link:** {message.text}",
                reply_markup=get_post_editor_keyboard()
            )
            message.stop_propagation()

# ==========================================
#  EXISTING COMMANDS & LOGIC
# ==========================================

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    
    if not await users_db.find_one({"_id": user_id}):
        await users_db.insert_one({"_id": user_id, "name": user_name})
        await send_log(f"👤 **New User Alert**\nName: {user_name}\nID: `{user_id}`")

    if len(message.command) > 1:
        not_joined = await check_all_subs(user_id)
        if not_joined:
            btns = []
            for ch in not_joined:
                try:
                    c = await client.get_chat(ch["_id"])
                    btns.append([InlineKeyboardButton(f"Join {c.title}", url=c.invite_link or f"https://t.me/{c.username}")])
                except:
                    continue
            btns.append([InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{client.me.username}?start={message.command[1]}")])
            return await message.reply("⛔ Join all channels to get files:", reply_markup=InlineKeyboardMarkup(btns))
        
        try:
            batch_id = decode(message.command[1])
            batch = await batches_db.find_one({"_id": batch_id})
            if not batch:
                return await message.reply("❌ Batch not found.")
            
            files_info = "\n".join([f"• {f.get('caption', 'Untitled Media')[:40]}..." for f in batch['files']])
            await send_log(f"📂 **Detailed File Access**\n\n👤 User: {user_name}\n🆔 ID: `{user_id}`\n📦 Batch ID: `{batch_id}`\n\n📄 **Files:**\n{files_info}")
            
            brand_data = await get_config("brand_config")
            brand_btn = None
            if brand_data:
                brand_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Join Main Channel", url=brand_data['link'])]])
            
            sent_msgs = []
            sorted_files = sorted(batch["files"], key=lambda x: x.get("msg_id", 0))
            for f in sorted_files:
                m = await client.send_cached_media(user_id, file_id=f["file_id"], caption=f.get("caption", ""), reply_markup=brand_btn)
                sent_msgs.append(m.id)
            
            del_msg = await message.reply(f"⏰ Files will delete in {AUTO_DELETE_TIME//3600} hours.")
            await asyncio.sleep(AUTO_DELETE_TIME)
            for mid in sent_msgs:
                try:
                    await client.delete_messages(user_id, mid)
                except:
                    pass
            await del_msg.delete()
            return
        except:
            return await message.reply("❌ Invalid Link.")

    welcome_msg_data = await get_config("welcome_msg")
    welcome_text = welcome_msg_data["text"] if welcome_msg_data else f"👋 Hi {user_name}! Use /store to create a batch."
    
    start_media_data = await get_config("start_media")
    if start_media_data:
        await client.send_cached_media(user_id, file_id=start_media_data["file_id"], caption=welcome_text)
    else:
        await message.reply(welcome_text)

# --- ADMIN COMMANDS ---
@app.on_message(filters.command("settings") & filters.user(ADMINS))
async def settings_cmd(client, message):
    mode_data = await get_config("bot_mode")
    mode = mode_data["mode"] if mode_data else "User"
    btn = [[InlineKeyboardButton(f"🤖 Bot Mode: {mode}", callback_data="toggle_mode")],
           [InlineKeyboardButton("❌ Close", callback_data="close_admin")]]
    await message.reply("🛠 **Bot Mode Settings**", reply_markup=InlineKeyboardMarkup(btn))

@app.on_message(filters.command("batches") & filters.user(ADMINS))
async def batches_cmd(client, message):
    btn = [[InlineKeyboardButton("📜 List All (TXT)", callback_data="list_all_b")],
           [InlineKeyboardButton("🗑 Delete ID", callback_data="del_b_id")],
           [InlineKeyboardButton("🧨 Clear All Batches", callback_data="clear_all_confirm")],
           [InlineKeyboardButton("❌ Close", callback_data="close_admin")]]
    await message.reply("📦 **Batch Management Panel**", reply_markup=InlineKeyboardMarkup(btn))

@app.on_message(filters.command("forcesub") & filters.user(ADMINS))
async def forcesub_cmd(client, message):
    btn = [[InlineKeyboardButton("➕ Add Channel", callback_data="add_ch"), 
            InlineKeyboardButton("➖ Delete Channel", callback_data="del_ch")],
           [InlineKeyboardButton("📜 List Channels", callback_data="list_chs")],
           [InlineKeyboardButton("❌ Close", callback_data="close_admin")]]
    await message.reply("📢 **ForceSub Manager**", reply_markup=InlineKeyboardMarkup(btn))

@app.on_message(filters.command("brand") & filters.user(ADMINS))
async def brand_cmd(client, message):
    brand_data = await get_config("brand_config")
    curr = brand_data["link"] if brand_data else "Not Set"
    btn = [[InlineKeyboardButton("➕ Set Brand Channel", callback_data="set_brand")],
           [InlineKeyboardButton("🗑 Remove Brand Button", callback_data="rm_brand")],
           [InlineKeyboardButton("❌ Close", callback_data="close_admin")]]
    await message.reply(f"🏷 **Branding Manager**\nCurrent Link: {curr}", reply_markup=InlineKeyboardMarkup(btn))

@app.on_message(filters.command("wallpaper") & filters.user(ADMINS))
async def wallpaper_cmd(client, message):
    curr = await get_config("start_media")
    status = "✅ Media is Set" if curr else "❌ No Media Set"
    btn = [[InlineKeyboardButton("🖼 Set / Change Media", callback_data="set_start_media")],
           [InlineKeyboardButton("🗑 Remove Media", callback_data="rm_start_media")],
           [InlineKeyboardButton("❌ Close", callback_data="close_admin")]]
    await message.reply(f"🖼 **Wallpaper Settings**\nStatus: {status}", reply_markup=InlineKeyboardMarkup(btn))

@app.on_message(filters.command("welcome") & filters.user(ADMINS))
async def welcome_cmd(client, message):
    curr = await get_config("welcome_msg")
    text = curr["text"] if curr else "Default"
    btn = [[InlineKeyboardButton("📝 Edit Welcome Msg", callback_data="set_welcome_msg")],
           [InlineKeyboardButton("🗑 Reset Message", callback_data="rm_welcome_msg")],
           [InlineKeyboardButton("❌ Close", callback_data="close_admin")]]
    await message.reply(f"👋 **Welcome Message Manager**\n\nCurrent Text:\n`{text}`", reply_markup=InlineKeyboardMarkup(btn))

@app.on_message(filters.command("stats") & filters.user(ADMINS))
async def stats_cmd(client, message):
    u = await users_db.count_documents({})
    b = await batches_db.count_documents({})
    await message.reply(f"📊 **Bot Stats:** Users `{u}` | Batches `{b}`")

@app.on_message(filters.command("broadcast") & filters.user(ADMINS))
async def broadcast_cmd(client, message):
    if not message.reply_to_message:
        return await message.reply("Reply to any message to broadcast.")
    users = users_db.find({})
    count = 0
    async for u in users:
        try:
            await message.reply_to_message.copy(u["_id"])
            count += 1
        except:
            pass
    await message.reply(f"📢 Broadcast Finished: {count} users.")

# --- STORE LOGIC ---

@app.on_message(filters.command("store") & filters.private)
async def store_init(client, message):
    mode_data = await get_config("bot_mode")
    if mode_data and mode_data["mode"] == "Admin" and message.from_user.id not in ADMINS:
        return
    user_data[message.from_user.id] = []
    # Clear Post Maker data to avoid conflict
    post_maker_data.pop(message.from_user.id, None)
    await message.reply("📥 **Batch Started!** Send Photos, Videos, or Files now.")

@app.on_message(filters.private & filters.media)
async def collector(client, message):
    u_id = message.from_user.id
    
    if u_id in admin_state:
        state = admin_state[u_id]
        if state == "set_start_media":
            media_item = (message.document or message.video or (message.photo[-1] if message.photo else None))
            if media_item:
                await settings_db.update_one({"id": "start_media"}, {"$set": {"file_id": media_item.file_id}}, upsert=True)
                await message.reply("✅ Wallpaper Updated!")
                admin_state.pop(u_id, None)
                return
        
        elif state == "set_welcome_msg":
            await settings_db.update_one({"id": "welcome_msg"}, {"$set": {"text": message.text.html if message.text else message.caption.html}}, upsert=True)
            await message.reply("✅ Welcome Message Updated!")
            admin_state.pop(u_id, None)
            return

    if u_id not in user_data:
        return
    
    media_id = None
    if message.photo:
        media_id = message.photo[-1].file_id
    elif message.document:
        media_id = message.document.file_id
    elif message.video:
        media_id = message.video.file_id
    elif message.audio:
        media_id = message.audio.file_id
    elif message.voice:
        media_id = message.voice.file_id
    elif message.video_note:
        media_id = message.video_note.file_id

    if not media_id:
        return

    cap = clean_caption(message.caption.html if message.caption else "")
    user_data[u_id].append({"msg_id": message.id, "file_id": media_id, "caption": cap})
    
    if u_id in waiting_tasks:
        waiting_tasks[u_id].cancel()

    async def delayed_reply():
        try:
            await asyncio.sleep(2.5)
            if u_id in last_ui_msg:
                try:
                    await client.delete_messages(u_id, last_ui_msg[u_id])
                except:
                    pass
            
            await send_log(f"📥 **Storage Update**\nAdmin: {message.from_user.first_name}\nMedia added: {len(user_data[u_id])}")
            user_data[u_id].sort(key=lambda x: x["msg_id"])
            
            btn = [[InlineKeyboardButton("📝 Edit Last Caption", callback_data=f"edit_cap_{len(user_data[u_id])-1}")],
                   [InlineKeyboardButton("➕ Add More", callback_data="add_more")],
                   [InlineKeyboardButton("🔗 Get Link", callback_data="get_link"), InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
            
            rep = await message.reply(f"✅ **Batch Updated! Total: {len(user_data[u_id])}**", reply_markup=InlineKeyboardMarkup(btn))
            last_ui_msg[u_id] = rep.id
        except asyncio.CancelledError:
            pass

    waiting_tasks[u_id] = asyncio.create_task(delayed_reply())

# --- CALLBACK HANDLER ---

@app.on_callback_query()
async def cb_handler(client, query: CallbackQuery):
    u_id = query.from_user.id
    data = query.data

    # --- POST MAKER CALLBACKS ---
    if data.startswith("pm_"):
        if u_id not in post_maker_data:
            await query.answer("Session expired. /edit_link again.", show_alert=True)
            return

        if data == "pm_set_details":
            post_maker_data[u_id]["step"] = "waiting_for_caption"
            await query.message.edit_caption(
                "📝 **Apna Caption bhejein:**\n(Jo photo ke neeche likhna hai)",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="pm_back")]])
            )

        elif data == "pm_set_link":
            post_maker_data[u_id]["step"] = "waiting_for_link"
            await query.message.edit_caption(
                "🔗 **Link bhejein:**\n(Jo button me lagega)",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="pm_back")]])
            )

        elif data == "pm_back":
            post_maker_data[u_id]["step"] = "menu_mode"
            cap = post_maker_data[u_id].get("caption", "No details.")
            lnk = post_maker_data[u_id].get("link", "Not Set")
            await query.message.edit_caption(
                f"⚙️ **MENU**\n\n📝 **Details:** {cap}\n🔗 **Link:** {lnk}",
                reply_markup=get_post_editor_keyboard()
            )

        elif data == "pm_generate":
            link = post_maker_data[u_id].get("link")
            caption = post_maker_data[u_id].get("caption", "Click below 👇")
            photo = post_maker_data[u_id].get("photo")

            if not link:
                await query.answer("⚠️ Pehle Link to add karo!", show_alert=True)
                return

            # --- FINAL BUTTONS FOR POST MAKER ---
            final_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Download / Watch", url=link)],
                # 👇 YAHAN APNE CHANNEL KA LINK DALEIN 👇
                [InlineKeyboardButton("🔔 Join Channel", url="https://t.me/ANIME_HUB_CLASSIC")] 
            ])

            await query.message.reply_photo(photo=photo, caption=caption, reply_markup=final_keyboard)
            await query.message.delete()
            await query.message.reply_text("✅ **Done!** Aapka post taiyar hai.")
            del post_maker_data[u_id]

        elif data == "pm_cancel":
            del post_maker_data[u_id]
            await query.message.edit_caption("❌ Cancelled.")
        return

    # --- EXISTING CALLBACKS ---

    if data == "get_link":
        if u_id not in user_data or not user_data[u_id]:
            return
        user_data[u_id].sort(key=lambda x: x["msg_id"])
        b_id = str(uuid.uuid4())[:8]
        
        filenames = "\n".join([f"• {f.get('caption', 'Media')[:30]}..." for f in user_data[u_id]])
        
        await batches_db.insert_one({"_id": b_id, "files": user_data[u_id]})
        link = f"https://t.me/{client.me.username}?start={encode(b_id)}"
        
        btn = [[InlineKeyboardButton("🔗 Copy Link", url=f"https://t.me/share/url?url={link}")],
               [InlineKeyboardButton("➕ Start New Batch", callback_data="add_more")]]
        
        await query.edit_message_text(f"🏁 **Batch Saved!**\n\nLink: `{link}`", reply_markup=InlineKeyboardMarkup(btn))
        await send_log(f"✅ **Permanent Batch Saved**\n\n🆔 Batch ID: `{b_id}`\n👤 Admin: {query.from_user.first_name}\n📦 Total: {len(user_data[u_id])}\n📄 **List:**\n{filenames}\n\n🔗 Link: {link}")
        user_data.pop(u_id, None)

    elif data == "toggle_mode":
        curr_data = await get_config("bot_mode")
        curr = curr_data["mode"] if curr_data else "User"
        new = "Admin" if curr == "User" else "User"
        await settings_db.update_one({"id": "bot_mode"}, {"$set": {"mode": new}}, upsert=True)
        await query.edit_message_reply_markup(InlineKeyboardMarkup([[InlineKeyboardButton(f"🤖 Bot Mode: {new}", callback_data="toggle_mode")], [InlineKeyboardButton("❌ Close", callback_data="close_admin")]]))

    elif data == "set_start_media":
        admin_state[u_id] = "set_start_media"
        await query.edit_message_text("🖼 Send the Photo or Video for Wallpaper:")

    elif data == "rm_start_media":
        await settings_db.delete_one({"id": "start_media"})
        await query.answer("Wallpaper Removed!")
        await query.message.delete()

    elif data == "set_welcome_msg":
        admin_state[u_id] = "set_welcome_msg"
        await query.edit_message_text("📝 Send the New Welcome Message:")

    elif data == "rm_welcome_msg":
        await settings_db.delete_one({"id": "welcome_msg"})
        await query.answer("Message Reset!")
        await query.message.delete()

    elif data == "rm_brand":
        await settings_db.delete_one({"id": "brand_config"})
        await query.answer("Branding Removed!")
        await query.message.delete()

    elif data == "clear_all_exec":
        await batches_db.delete_many({})
        await query.answer("All links deleted!", show_alert=True)
        await query.message.delete()

    elif data == "list_all_b":
        await query.answer("Please wait...")
        t = "FULL BATCH LIST\n"
        async for b in batches_db.find({}):
            t += f"ID: {b['_id']} | Media: {len(b['files'])}\n"
        with open("batch_report.txt", "w") as f:
            f.write(t)
        await query.message.reply_document("batch_report.txt")
        os.remove("batch_report.txt")

    elif data == "add_more" or data == "close_admin":
        await query.message.delete()

    elif data == "cancel":
        user_data.pop(u_id, None)
        await query.message.edit_text("❌ Batch Process Cancelled.")

    elif data == "add_ch":
        admin_state[u_id] = "add_ch"
        await query.edit_message_text("Send ID or Username of Channel:")

    elif data == "del_ch":
        admin_state[u_id] = "del_ch"
        await query.edit_message_text("Send ID to Delete:")

    elif data == "list_chs":
        t = "ForceSub Channels:\n"
        async for c in channels_db.find({}):
            t += f"- {c['title']} (`{c['_id']}`)\n"
        await query.edit_message_text(t, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="close_admin")]]))

    elif data == "del_b_id":
        admin_state[u_id] = "del_b_id"
        await query.edit_message_text("Send Batch ID to Delete:")

    elif data == "set_brand":
        admin_state[u_id] = "set_brand"
        await query.edit_message_text("Send Brand Channel username/ID:")

    elif data == "clear_all_confirm":
        await query.edit_message_text("⚠️ **DANGER!**\nAre you sure you want to delete ALL links?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Confirm Delete", callback_data="clear_all_exec")], [InlineKeyboardButton("❌ Cancel", callback_data="close_admin")]]))

    elif data.startswith("edit_cap_"):
        idx = int(data.split("_")[2])
        admin_state[u_id] = {"action": "edit_cap", "index": idx}
        await query.edit_message_text(f"📝 Send new caption for file {idx + 1}:")

# --- ADMIN INPUT HANDLER ---
@app.on_message(filters.private & filters.user(ADMINS), group=1)
async def admin_inputs(client, message):
    a_id = message.from_user.id
    if a_id not in admin_state:
        return
    state = admin_state[a_id]
    text = message.text.strip() if message.text else ""
    try:
        if state in ["add_ch", "set_brand"]:
            # Input parsing logic
            target = f"-100{text}" if text.isdigit() and not text.startswith("-") else text
            chat = await client.get_chat(target)
            if state == "add_ch":
                await channels_db.update_one({"_id": chat.id}, {"$set": {"title": chat.title}}, upsert=True)
                await message.reply(f"✅ Added ForceSub: {chat.title}")
            elif state == "set_brand":
                link = chat.invite_link or f"https://t.me/{chat.username}"
                await settings_db.update_one({"id": "brand_config"}, {"$set": {"link": link}}, upsert=True)
                await message.reply(f"✅ Branding Set: {link}")
        
        elif state == "del_ch":
            target = f"-100{text}" if text.isdigit() and not text.startswith("-") else text
            await channels_db.delete_one({"_id": int(target) if isinstance(target, str) and target.startswith("-") else target})
            await message.reply("✅ Channel Removed.")

        elif state == "del_b_id":
            res = await batches_db.delete_one({"_id": text})
            if res.deleted_count > 0:
                await message.reply(f"✅ Deleted Batch ID: `{text}`")
                await send_log(f"🗑 **Batch Deleted:** `{text}`")
            else:
                await message.reply("❌ Batch ID not found.")

        elif isinstance(state, dict) and state.get("action") == "edit_cap":
            idx = state["index"]
            user_data[a_id][idx]["caption"] = message.text.html
            btns = [[InlineKeyboardButton("➕ Add More", callback_data="add_more")], [InlineKeyboardButton("🔗 Get Link", callback_data="get_link"), InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
            await message.reply(f"✅ Caption Updated! Total Files: {len(user_data[a_id])}", reply_markup=InlineKeyboardMarkup(btns))

    except Exception as e:
        await message.reply(f"❌ Error: {e}")
    
    admin_state.pop(a_id, None)

# --- STARTUP ---
async def start_services():
    Thread(target=run_flask, daemon=True).start()
    await app.start()
    await app.set_bot_commands([
        BotCommand("start", "Start Bot"), 
        BotCommand("store", "Create Multi-Media Batch"),
        BotCommand("edit_link", "Create Post with Button"), # Added Command
        BotCommand("settings", "Bot Mode Settings"), 
        BotCommand("batches", "Link Manager"),
        BotCommand("forcesub", "Channel Manager"), 
        BotCommand("brand", "Branding Settings"),
        BotCommand("wallpaper", "Wallpaper Settings"), 
        BotCommand("welcome", "Custom Welcome Text"),
        BotCommand("stats", "Live Statistics"), 
        BotCommand("broadcast", "Send news to Users")
    ])
    await send_log("🚀 **Bot V21 Pro Max - Live & Stable!**\nAll Media Systems Active.")
    await idle()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(start_services())