# logger.py
from pyrogram import Client
from pyrogram.types import Message
from config import Config
import os

async def log_file(client: Client, message: Message, file_path: str, new_filename: str, user, thumb_path: str = None):
    """
    Uploads renamed/auto-renamed video/file to log channel with:
    - New filename in bold
    - Thumbnail (if available)
    - Metadata (duration, size, caption)
    Works for both videos and documents.
    """

    try:
        caption_text = f"**{new_filename}**\n\n📂 Renamed by: {user.mention}"

        if message.video or file_path.lower().endswith(('.mp4', '.mkv', '.avi', '.mov')):
            # Send as video
            await client.send_video(
                chat_id=Config.LOG_CHANNEL,
                video=file_path,
                caption=caption_text,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                duration=message.video.duration if message.video else 0,
                supports_streaming=True
            )
        else:
            # Send as document
            await client.send_document(
                chat_id=Config.LOG_CHANNEL,
                document=file_path,
                caption=caption_text,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None
            )

    except Exception as e:
        print(f"[LOGGER ERROR] {e}")
