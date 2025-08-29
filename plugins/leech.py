import os
import aiohttp
import subprocess
import time
from pyrogram import Client, filters
from pyrogram.types import Message

# If you created logger.py as we discussed:
#   logger.log_file(client, file_path, new_filename, user)
from logger import log_file  # make sure logger.py is in PYTHONPATH or same folder


# ---------------- Utils ---------------- #

def humanbytes(size: float) -> str:
    if size is None:
        return "0 B"
    power = 1024
    n = 0
    units = ["B", "KB", "MB", "GB", "TB"]
    while size >= power and n < len(units) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {units[n]}"

def fmt_eta(seconds: int) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(max(0, int(seconds))))


# ---------------- Core helpers ---------------- #

async def download_file(url: str, filename: str, status_msg: Message = None):
    """
    Download with Telegram progress. Handles:
    - custom headers (for CDNs like multiquality / cloudflare-lite)
    - unknown Content-Length (shows bytes + speed)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "*/*",
        "Connection": "keep-alive",
        "Referer": url.split("/")[0] + "//" + url.split("/")[2] if "://" in url else ""
    }

    total_size = 0
    downloaded = 0
    last_edit = 0.0
    start = time.time()

    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, allow_redirects=True) as resp:
            if resp.status != 200:
                raise Exception(f"Download failed: HTTP {resp.status}")

            # Try to read content length; may be absent for some CDNs
            cl = resp.headers.get("Content-Length")
            try:
                total_size = int(cl) if cl else 0
            except:
                total_size = 0

            with open(filename, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1 MB
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    # Throttle message edits to avoid flood (update ~ every 2s or on finish)
                    now = time.time()
                    if (now - last_edit > 2) or (total_size and downloaded == total_size):
                        last_edit = now
                        elapsed = now - start
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        if total_size:
                            pct = downloaded * 100 / total_size
                            bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
                            eta = (total_size - downloaded) / speed if speed > 0 else 0
                            txt = (
                                f"📥 **Downloading...**\n"
                                f"`{os.path.basename(filename)}`\n"
                                f"[{bar}] {pct:.2f}%\n"
                                f"**Done:** {humanbytes(downloaded)} / {humanbytes(total_size)}\n"
                                f"**Speed:** {humanbytes(speed)}/s | **ETA:** {fmt_eta(eta)}"
                            )
                        else:
                            # Unknown total size
                            txt = (
                                f"📥 **Downloading...**\n"
                                f"`{os.path.basename(filename)}`\n"
                                f"**Done:** {humanbytes(downloaded)} (total size unknown)\n"
                                f"**Speed:** {humanbytes(speed)}/s"
                            )
                        if status_msg:
                            try:
                                await status_msg.edit_text(txt)
                            except:
                                pass
    return filename


async def fix_metadata(input_file: str, output_file: str):
    """
    Remux to ensure proper duration (fix 0:00 bug) without re-encoding.
    """
    cmd = [
        "ffmpeg", "-i", input_file,
        "-c", "copy", "-map", "0",
        "-movflags", "+faststart",
        "-fflags", "+genpts",
        "-y", output_file
    ]
    subprocess.run(cmd, check=True)
    return output_file


async def cleanup_file(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"[CLEANUP] {e}")


async def upload_progress(current: int, total: int, status_msg: Message, start_time: float, label: str):
    """
    Pyrogram upload progress callback. Throttled inside send_* by pyrogram; we
    keep our own throttle by only recalculating text here.
    """
    elapsed = time.time() - start_time
    speed = current / elapsed if elapsed > 0 else 0
    pct = current * 100 / total if total else 0
    eta = (total - current) / speed if speed > 0 and total else 0
    bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))

    txt = (
        f"{label}\n"
        f"[{bar}] {pct:.2f}%\n"
        f"**Done:** {humanbytes(current)} / {humanbytes(total)}\n"
        f"**Speed:** {humanbytes(speed)}/s | **ETA:** {fmt_eta(eta)}"
    )
    try:
        await status_msg.edit_text(txt)
    except:
        pass


async def process_video(url: str, new_name: str, chat_id: int, client: Client, status_msg: Message):
    """
    Leech -> Rename -> Fix Metadata -> Upload to user (with progress) -> Log to LOG_CHANNEL -> Cleanup
    """
    # temp paths
    dl_dir = "downloads"
    os.makedirs(dl_dir, exist_ok=True)
    temp_file = os.path.join(dl_dir, "temp_download.mp4")

    # ensure .mp4 name
    renamed_file = new_name if new_name.lower().endswith(".mp4") else f"{new_name}.mp4"
    renamed_path = os.path.join(dl_dir, renamed_file)
    fixed_path = os.path.join(dl_dir, f"fixed_{renamed_file}")

    # 1) Download
    await status_msg.edit_text("📥 Starting download...")
    await download_file(url, temp_file, status_msg)

    # 2) Rename
    try:
        if os.path.exists(renamed_path):
            os.remove(renamed_path)
        os.rename(temp_file, renamed_path)
    except Exception as e:
        raise Exception(f"Rename failed: {e}")

    # 3) Fix metadata
    await status_msg.edit_text("⚒️ Fixing metadata...")
    await fix_metadata(renamed_path, fixed_path)

    # 4) Upload to user with progress
    await status_msg.edit_text("📤 Uploading to Telegram...")
    up_start = time.time()
    sent = await client.send_video(
        chat_id=chat_id,
        video=fixed_path,
        caption=renamed_file,  # keep new filename as caption to user; change if you want empty
        progress=upload_progress,
        progress_args=(status_msg, up_start, "📤 Uploading to Telegram..."),
    )

    # 5) Also log to LOG_CHANNEL using your logger.py (bold filename, reply "renamed by user")
    try:
        await log_file(client, fixed_path, renamed_file, user=sent.from_user or None)
    except Exception as e:
        # If sent.from_user is None (rare), fall back to original message user
        try:
            await log_file(client, fixed_path, renamed_file, user=(await client.get_users(chat_id)))
        except:
            print(f"[LOGGER] {e}")

    # 6) Cleanup
    await status_msg.delete()
    await cleanup_file(temp_file)
    await cleanup_file(renamed_path)
    await cleanup_file(fixed_path)


# ---------------- Command wiring ---------------- #

def add_handlers(app: Client):
    @app.on_message(filters.command("leech") & filters.private)
    async def leech_handler(client: Client, message: Message):
        if len(message.command) < 3:
            return await message.reply("⚡ Usage: `/leech <direct_link> <new_name>`", quote=True)

        url = message.command[1]
        new_name = " ".join(message.command[2:]).strip()

        status_msg = await message.reply("📥 Preparing...", quote=True)
        try:
            await process_video(url, new_name, message.chat.id, client, status_msg)
        except Exception as e:
            try:
                await status_msg.edit_text(f"❌ Error: `{e}`")
            except:
                await message.reply(f"❌ Error: `{e}`")
