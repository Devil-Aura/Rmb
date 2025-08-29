import os
import aiohttp
import subprocess
from pyrogram import Client, filters


# ================= Helpers ================= #

async def download_file(url: str, filename: str):
    """Leech (download) any direct link"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Download failed: {resp.status}")
            with open(filename, "wb") as f:
                while True:
                    chunk = await resp.content.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
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


async def process_video(url: str, new_name: str, chat_id: int, client: Client):
    """Leech -> Rename -> Fix Metadata -> Upload -> Clean"""
    temp_file = "temp_download.mp4"
    renamed_file = new_name if new_name.endswith(".mp4") else f"{new_name}.mp4"
    fixed_file = f"fixed_{renamed_file}"

    # 1. Download
    await download_file(url, temp_file)

    # 2. Rename
    os.rename(temp_file, renamed_file)

    # 3. Fix Metadata
    await fix_metadata(renamed_file, fixed_file)

    # 4. Send to Telegram
    await client.send_video(chat_id, fixed_file, caption=new_name)

    # 5. Cleanup
    await cleanup_file(temp_file)
    await cleanup_file(renamed_file)
    await cleanup_file(fixed_file)


# ================= Command ================= #

def add_handlers(app: Client):
    @app.on_message(filters.command("leech") & filters.private)
    async def leech_handler(client, message):
        if len(message.command) < 3:
            return await message.reply("⚡ Usage: `/leech <direct_link> <new_name>`")

        url = message.command[1]
        new_name = " ".join(message.command[2:])

        await message.reply("📥 Downloading & Processing, please wait...")

        try:
            await process_video(url, new_name, message.chat.id, client)
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
