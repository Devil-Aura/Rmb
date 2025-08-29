# leech.py
import os
import aiohttp
import time
import uuid
from pyrogram import Client, filters
from pyrogram.types import Message
from logger import log_file  # make sure logger.py is in same folder

# ---------------- Utils ---------------- #
def humanbytes(size: float) -> str:
    if size is None: return "0 B"
    power = 1024
    n = 0
    units = ["B", "KB", "MB", "GB", "TB"]
    while size >= power and n < len(units) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {units[n]}"

def fmt_eta(seconds: int) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(max(0, int(seconds))))

async def download_file(url: str, filename: str, status_msg: Message = None):
    headers = {"User-Agent": "Mozilla/5.0"}
    total_size = 0
    downloaded = 0
    last_edit = 0.0
    start = time.time()
    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"Download failed: HTTP {resp.status}")
            cl = resp.headers.get("Content-Length")
            total_size = int(cl) if cl else 0
            with open(filename, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if (now - last_edit > 2) or (total_size and downloaded == total_size):
                        last_edit = now
                        elapsed = now - start
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        pct = downloaded * 100 / total_size if total_size else 0
                        bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
                        eta = (total_size - downloaded) / speed if speed > 0 else 0
                        txt = f"📥 Downloading `{os.path.basename(filename)}`\n[{bar}] {pct:.2f}%\n**Done:** {humanbytes(downloaded)} / {humanbytes(total_size)}\n**Speed:** {humanbytes(speed)}/s | ETA: {fmt_eta(eta)}" if total_size else f"📥 Downloading `{os.path.basename(filename)}`\n**Done:** {humanbytes(downloaded)}"
                        if status_msg:
                            try: await status_msg.edit_text(txt)
                            except: pass
    return filename

async def cleanup_file(path: str):
    try:
        if path and os.path.exists(path): os.remove(path)
    except Exception as e:
        print(f"[CLEANUP] {e}")

# ---------------- Core Processor ---------------- #
async def process_file(url: str, new_name: str, chat_id: int, client: Client, status_msg: Message, message: Message):
    dl_dir = "downloads"
    os.makedirs(dl_dir, exist_ok=True)
    ext = os.path.splitext(url)[1].lower() or ".mp4"
    unique_id = str(uuid.uuid4())[:8]
    temp_file = os.path.join(dl_dir, f"temp_{unique_id}{ext}")
    final_name = new_name + ext if not new_name.endswith(ext) else new_name
    final_path = os.path.join(dl_dir, final_name)

    # 1) Download
    await status_msg.edit_text("📥 Starting download...")
    await download_file(url, temp_file, status_msg)

    # 2) Rename/move
    try:
        if os.path.exists(final_path): os.remove(final_path)
        os.rename(temp_file, final_path)
    except Exception as e:
        raise Exception(f"Rename failed: {e}")

    # 3) Upload to user
    await status_msg.edit_text("📤 Uploading to Telegram...")
    if ext in [".mp4", ".mkv", ".avi", ".mov"]:
        await client.send_video(chat_id=chat_id, video=final_path, caption=final_name)
    else:
        await client.send_document(chat_id=chat_id, document=final_path, caption=final_name)

    # 4) Log to LOG_CHANNEL
    try:
        await log_file(client=client, message=message, file_path=final_path, new_filename=final_name, user=message.from_user)
    except Exception as e:
        print(f"[LOGGER ERROR] {e}")

    # 5) Cleanup
    await cleanup_file(final_path)
    await status_msg.delete()

# ---------------- Command Handler ---------------- #
def add_handlers(app: Client):
    @app.on_message(filters.command("leech") & filters.private)
    async def leech_handler(client: Client, message: Message):
        if len(message.command) < 3:
            return await message.reply("⚡ Usage: `/leech <direct_link> <new_name>`", quote=True)
        url = message.command[1]
        new_name = " ".join(message.command[2:]).strip()
        status_msg = await message.reply("📥 Preparing download...", quote=True)
        try:
            await process_file(url, new_name, message.chat.id, client, status_msg, message)
        except Exception as e:
            try: await status_msg.edit_text(f"❌ Error: `{e}`")
            except: await message.reply(f"❌ Error: `{e}`", quote=True)
