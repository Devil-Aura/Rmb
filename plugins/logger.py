# logger.py
from pyrogram import Client
from pyrogram.types import Message
from config import Config

async def log_video(client: Client, message: Message, file_path: str, new_filename: str, user, thumb_path: str = None):
    """
    Uploads renamed video/file to log channel as VIDEO with the new filename only (bold).
    Then replies under that log file with 'This file was renamed by <user>'
    """
    try:
        # Send video with applied thumbnail and only new filename in bold
        sent = await client.send_video(
            chat_id=Config.LOG_CHANNEL,
            video=file_path,
            thumb=thumb_path if thumb_path else None,
            caption=f"**{new_filename}**"
        )

        # Reply under that video
        await sent.reply_text(
            f"This file was renamed by {user.mention}"
        )

    except Exception as e:
        print(f"Error in log_video: {e}")
