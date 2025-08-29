import os
from pyrogram import Client, filters
from helper.ffmpeg import download_file, fix_metadata, cleanup_file


@Client.on_message(filters.command("leech") & filters.private)
async def leech_handler(client, message):
    if len(message.command) < 2:
        return await message.reply("Usage: /leech <direct_link> [New Name]")

    url = message.command[1]
    new_name = " ".join(message.command[2:]) or "video.mp4"
    if not new_name.endswith(".mp4"):
        new_name += ".mp4"

    temp_file = "temp_download.mp4"
    renamed_file = new_name
    fixed_file = f"fixed_{new_name}"

    status = await message.reply("📥 Downloading & Processing, please wait...")

    try:
        # 1. Download (leech)
        await download_file(url, temp_file)

        # 2. Rename
        os.rename(temp_file, renamed_file)

        # 3. Fix metadata
        await fix_metadata(renamed_file, fixed_file)

        # 4. Send to Telegram
        await client.send_video(message.chat.id, fixed_file, caption=new_name)

        await status.edit("✅ Done! Uploaded successfully.")

    except Exception as e:
        await status.edit(f"❌ Error: {str(e)}")

    finally:
        # 5. Cleanup temp files
        await cleanup_file(temp_file)
        await cleanup_file(renamed_file)
        await cleanup_file(fixed_file)
