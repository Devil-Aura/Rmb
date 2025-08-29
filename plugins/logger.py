from pyrogram import Client
from pyrogram.types import Message
from config import LOG_CHANNEL

async def log_file(client: Client, message: Message, file_path: str, new_filename: str, user):
    """
    Uploads renamed/leeched file to log channel with new filename only (bold).
    Then replies under that log file with 'This file was renamed by <user>'
    """
    try:
        # Send file with new filename (caption = bold filename only)
        sent_msg = await client.send_document(
            chat_id=LOG_CHANNEL,
            document=file_path,
            caption=f"**{new_filename}**"
        )

        # Reply under that file in log channel with info
        await client.send_message(
            chat_id=LOG_CHANNEL,
            text=f"This file was renamed by {user.mention}",
            reply_to_message_id=sent_msg.message_id   # ✅ Correct attribute
        )

    except Exception as e:
        print(f"Log Upload Error: {e}")
