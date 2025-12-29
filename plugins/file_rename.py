from pyrogram import Client, filters
from pyrogram.enums import MessageMediaType
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from helper.ffmpeg import fix_thumb, take_screen_shot, add_metadata, fix_metadata
from helper.utils import progress_for_pyrogram, convert, humanbytes, add_prefix_suffix
from helper.database import jishubotz
from PIL import Image
import os, time, random, asyncio, subprocess

# ================== CONFIG ==================
LOG_CHANNEL = -1003058967184  # <-- Put your log channel ID here
MAX_CONCURRENT = 6            # Limit concurrent rename+upload tasks
semaphore = asyncio.Semaphore(MAX_CONCURRENT)
# ===========================================

def get_duration(path):
    """Get actual video/audio duration using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        return int(float(result.stdout))
    except:
        return 0


@Client.on_message(filters.private & (filters.document | filters.audio | filters.video))
async def rename_start(client, message):
    file = getattr(message, message.media.value)
    filename = file.file_name
    if file.file_size > 2000 * 1024 * 1024:
        return await message.reply_text(
            "❌ This bot doesn’t support files bigger than 2GB",
            quote=True
        )

    try:
        await message.reply_text(
            text=f"**Please Enter New Filename...**\n\n**Old File Name** :- `{filename}`",
            reply_to_message_id=message.id,
            reply_markup=ForceReply(True)
        )
        await asyncio.sleep(30)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await message.reply_text(
            text=f"**Please Enter New Filename...**\n\n**Old File Name** :- `{filename}`",
            reply_to_message_id=message.id,
            reply_markup=ForceReply(True)
        )
    except:
        pass


@Client.on_message(filters.private & filters.reply)
async def refunc(client, message):
    reply_message = message.reply_to_message
    if (reply_message.reply_markup) and isinstance(reply_message.reply_markup, ForceReply):
        new_name = message.text
        await message.delete()
        msg = await client.get_messages(message.chat.id, reply_message.id)
        file = msg.reply_to_message
        media = getattr(file, file.media.value)

        if "." not in new_name:
            if "." in media.file_name:
                extn = media.file_name.rsplit(".", 1)[-1]
            else:
                extn = "mkv"
            new_name = new_name + "." + extn
        await reply_message.delete()

        button = [[InlineKeyboardButton("📁 Document", callback_data="upload_document")]]
        if file.media in [MessageMediaType.VIDEO, MessageMediaType.DOCUMENT]:
            button.append([InlineKeyboardButton("🎥 Video", callback_data="upload_video")])
        elif file.media == MessageMediaType.AUDIO:
            button.append([InlineKeyboardButton("🎵 Audio", callback_data="upload_audio")])

        await message.reply(
            text=f"**Select The Output File Type**\n\n**File Name :-** `{new_name}`",
            reply_to_message_id=file.id,
            reply_markup=InlineKeyboardMarkup(button)
        )


@Client.on_callback_query(filters.regex("upload"))
async def doc(bot, update):
    asyncio.create_task(process_file(bot, update))  # run in background

async def process_file(bot, update):
    async with semaphore:  # limit concurrency
        os.makedirs("Metadata", exist_ok=True)

        # Get prefix/suffix
        prefix = await jishubotz.get_prefix(update.message.chat.id)
        suffix = await jishubotz.get_suffix(update.message.chat.id)

        new_name = update.message.text
        try:
            new_filename_ = new_name.split(":-")[1]
            new_filename = add_prefix_suffix(new_filename_, prefix, suffix)
        except Exception as e:
            return await update.message.edit(f"Prefix/Suffix Error: {e}")

        # Paths
        user_id = update.from_user.id
        download_path = f"downloads/{user_id}/{new_filename}"
        file_msg = update.message.reply_to_message

        ms = await update.message.edit("⬇️ Downloading...")
        try:
            path = await bot.download_media(
                message=file_msg,
                file_name=download_path,
                progress=progress_for_pyrogram,
                progress_args=("⬇️ Downloading...", ms, time.time())
            )
        except Exception as e:
            return await ms.edit(f"Download Error: {e}")

        # Add metadata
        metadata_enabled = await jishubotz.get_metadata(update.message.chat.id)
        metadata_failed = False
        if metadata_enabled:
            metadata_code = await jishubotz.get_metadata_code(update.message.chat.id)
            metadata_path = f"Metadata/{new_filename}"
            result = await add_metadata(path, metadata_path, metadata_code, ms)
            if result:
                metadata_path = result
            else:
                metadata_failed = True
                metadata_path = path  # fallback to original file
        else:
            metadata_path = path

        # Duration
        duration = get_duration(metadata_path)

        if duration == 0:
            try:
                await ms.edit("<i>Fixing Video Duration... ⚡</i>")
                fixed_path = await fix_metadata(metadata_path, f"{metadata_path}_fixed")
                # Update metadata_path to the fixed file
                if metadata_path != path and os.path.exists(metadata_path):
                     # If metadata_path was an intermediate file (not the original download), delete it
                     os.remove(metadata_path)
                metadata_path = fixed_path
                duration = get_duration(metadata_path)
            except Exception as e:
                print(f"Error fixing duration: {e}")

        # Caption
        media = getattr(file_msg, file_msg.media.value)
        user_caption = await jishubotz.get_caption(update.message.chat.id)
        if user_caption:
            try:
                caption = user_caption.format(
                    filename=new_filename,
                    filesize=humanbytes(media.file_size),
                    duration=convert(duration)
                )
            except:
                caption = f"**{new_filename}**"
        else:
            caption = f"**{new_filename}**"

        # Thumbnail
        c_thumb = await jishubotz.get_thumbnail(update.message.chat.id)
        if c_thumb:
            ph_path = await bot.download_media(c_thumb)
            width, height, ph_path = await fix_thumb(ph_path)
        else:
            try:
                ph_path_ = await take_screen_shot(
                    metadata_path,
                    os.path.dirname(metadata_path),
                    random.randint(0, max(duration - 1, 0))
                )
                width, height, ph_path = await fix_thumb(ph_path_)
            except:
                ph_path = None

        # Upload to user
        await ms.edit("⬆️ Uploading...")
        type_ = update.data.split("_")[1]
        sent_msg = None
        try:
            if type_ == "document":
                sent_msg = await bot.send_document(update.message.chat.id, document=metadata_path, thumb=ph_path, caption=caption)
            elif type_ == "video":
                sent_msg = await bot.send_video(update.message.chat.id, video=metadata_path, thumb=ph_path, caption=caption, duration=duration)
            elif type_ == "audio":
                sent_msg = await bot.send_audio(update.message.chat.id, audio=metadata_path, thumb=ph_path, caption=caption, duration=duration)
        except Exception as e:
            return await ms.edit(f"Upload Error: {e}")

        # ⚠️ Warning if metadata failed
        if metadata_failed and sent_msg:
            await sent_msg.reply_text("⚠️ Metadata could not be added. Uploaded original file instead.")

        # Forward to log channel
        try:
            if media.thumbs or c_thumb:
                thumb_for_log = ph_path
            else:
                thumb_for_log = None

            if file_msg.media == MessageMediaType.VIDEO:
                await bot.send_video(LOG_CHANNEL, video=metadata_path, caption=f"**{new_filename}**", thumb=thumb_for_log, duration=duration)
            elif file_msg.media == MessageMediaType.AUDIO:
                await bot.send_audio(LOG_CHANNEL, audio=metadata_path, caption=f"**{new_filename}**", thumb=thumb_for_log, duration=duration)
            else:
                await bot.send_document(LOG_CHANNEL, document=metadata_path, caption=f"**{new_filename}**")
        except Exception as e:
            print(f"Log Forward Error: {e}")

        # Cleanup
        if ph_path and os.path.exists(ph_path):
            os.remove(ph_path)
        if path and os.path.exists(path):
            os.remove(path)

        await ms.delete()
