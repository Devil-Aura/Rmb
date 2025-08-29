# logger.py
from pyrogram import Client
from pyrogram.types import Message
from config import Config
import os

async def log_file(client: Client, message: Message, file_path: str, new_filename: str, user, thumb_path: str = None):
    """
    Forward renamed files to the log channel:
    - Videos as videos
    - Documents/files as documents
    - Caption: new filename in bold
    - Preserve thumbnail if available
    """
    try:
        caption_text = f"**{new_filename}**"

        if message.video or file_path.lower().endswith(('.mp4', '.mkv', '.avi', '.mov')):
            # Forward as video
            await client.send_video(
                chat_id=Config.LOG_CHANNEL,
                video=file_path,
                caption=caption_text,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                duration=message.video.duration if message.video else 0,
                supports_streaming=True
            )
        else:
            # Forward as document
            await client.send_document(
                chat_id=Config.LOG_CHANNEL,
                document=file_path,
                caption=caption_text,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None
            )

    except Exception as e:
        print(f"[LOGGER ERROR] {e}")
