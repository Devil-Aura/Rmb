# logger.py
from pyrogram import Client
from pyrogram.types import Message
from config import Config

async def log_file(client: Client, message: Message, file_path: str, new_filename: str, user, thumb_path: str = None, duration: int = 0):
    """
    Uploads renamed file (video/document/audio) to log channel
    with the new filename in bold, then replies with 'This file was renamed by <user>'.
    """
    try:
        if file_path.lower().endswith((".mp4", ".mkv", ".mov")):
            sent = await client.send_video(
                chat_id=Config.LOG_CHANNEL,
                video=file_path,
                thumb=thumb_path if thumb_path else None,
                caption=f"**{new_filename}**",
                duration=duration if duration else None
            )
        elif file_path.lower().endswith((".mp3", ".m4a", ".wav", ".flac")):
            sent = await client.send_audio(
                chat_id=Config.LOG_CHANNEL,
                audio=file_path,
                thumb=thumb_path if thumb_path else None,
                caption=f"**{new_filename}**",
                duration=duration if duration else None
            )
        else:
            sent = await client.send_document(
                chat_id=Config.LOG_CHANNEL,
                document=file_path,
                thumb=thumb_path if thumb_path else None,
                caption=f"**{new_filename}**"
            )

        await sent.reply_text(f"This file was renamed by {user.mention}")

    except Exception as e:
        print(f"[LOGGER ERROR] {e}")
