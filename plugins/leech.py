import os
import aiohttp
import subprocess
from pyrogram import Client, filters


# ================= Helpers ================= #

async def download_file(url: str, filename: str, status_msg=None):
    """Leech (download) any direct link with Telegram progress bar"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Download failed: {resp.status}")

            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0

            with open(filename, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if total_size:
                        percent = (downloaded / total_size) * 100
                        bar = "█" * int(percent // 5) + "░" * (20 - int(percent // 5))
                        text = (
                            f"📥 **Downloading...**\n\n"
                            f"`{filename}`\n"
                            f"{bar} {percent:.2f}%\n"
                            f"{downloaded/1024/1024:.2f} MB / {total_size/1024/1024:.2f} MB"
                        )
                        try:
                            await status_msg.edit_text(text)
                        except:
                            pass
    return filename


async def fix_metadata(input_file: str, output_file: str):
    """Fix metadata so Telegram shows correct duration"""
    command = [
        "ffmpeg", "-i", input_file,
        "-c", "copy", "-map", "0",
        "-movflags", "+faststart",
        "-fflags", "+genpts",
        "-y", output_file
    ]
    subprocess.run(command, check=True)
    return output_file


async def cleanup_file(path: str):
    """Delete temp files"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"Cleanup failed: {e}")


async def process_video(url: str, new_name: str, chat_id: int, client: Client, status_msg):
    """Leech -> Rename -> Fix Metadata -> Upload -> Clean"""
    temp_file = "temp_download.mp4"
    renamed_file = new_name if new_name.endswith(".mp4") else f"{new_name}.mp4"
    fixed_file = f"fixed_{renamed_file}"

    # 1. Download with progress bar
    await download_file(url, temp_file, status_msg)

    # 2. Rename
    os.rename(temp_file, renamed_file)

    # 3. Fix Metadata
    await status_msg.edit_text("⚒️ Fixing Metadata...")
    await fix_metadata(renamed_file, fixed_file)

    # 4. Send to Telegram
    await status_msg.edit_text("📤 Uploading to Telegram...")
    await client.send_video(chat_id, fixed_file, caption=new_name)

    # 5. Cleanup
    await cleanup_file(temp_file)
    await cleanup_file(renamed_file)
    await cleanup_file(fixed_file)

    await status_msg.delete()


# ================= Command ================= #

def add_handlers(app: Client):
    @app.on_message(filters.command("leech") & filters.private)
    async def leech_handler(client, message):
        if len(message.command) < 3:
            return await message.reply("⚡ Usage: `/leech <direct_link> <new_name>`")

        url = message.command[1]
        new_name = " ".join(message.command[2:])

        status_msg = await message.reply("📥 Starting download...")

        try:
            await process_video(url, new_name, message.chat.id, client, status_msg)
        except Exception as e:
            await status_msg.edit_text(f"❌ Error: `{e}`")
