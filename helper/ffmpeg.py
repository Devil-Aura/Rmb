import time
import os
import asyncio
import aiohttp
import subprocess
from PIL import Image
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram.types import Message


def get_duration(path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if result.returncode == 0:
            return int(float(result.stdout.strip()))
    except Exception as e:
        print(f"FFprobe error: {e}")
    
    # Fallback to hachoir
    try:
        from hachoir.parser import createParser
        from hachoir.metadata import extractMetadata
        parser = createParser(path)
        if not parser:
            return 0
        metadata = extractMetadata(parser)
        if not metadata:
            return 0
        return int(metadata.get("duration").seconds)
    except Exception as e:
        print(f"Hachoir error: {e}")
        return 0

async def fix_thumb(thumb):
    width = 0
    height = 0
    try:
        if thumb is not None:
            parser = createParser(thumb)
            metadata = extractMetadata(parser)
            if metadata.has("width"):
                width = metadata.get("width")
            if metadata.has("height"):
                height = metadata.get("height")

            with Image.open(thumb) as img:
                img.convert("RGB").save(thumb)
                resized_img = img.resize((width, height))
                resized_img.save(thumb, "JPEG")
            parser.close()
    except Exception as e:
        print(e)
        thumb = None

    return width, height, thumb


async def take_screen_shot(video_file, output_directory, ttl):
    out_put_file_name = f"{output_directory}/{time.time()}.jpg"
    file_genertor_command = [
        "ffmpeg", "-ss", str(ttl), "-i", video_file,
        "-vframes", "1", out_put_file_name
    ]
    process = await asyncio.create_subprocess_exec(
        *file_genertor_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if os.path.lexists(out_put_file_name):
        return out_put_file_name
    return None


async def add_metadata(input_path, output_path, metadata, ms):
    try:
        await ms.edit("<i>I Found Metadata, Adding Into Your File ⚡</i>")
        command = [
            'ffmpeg', '-y', '-i', input_path, '-map', '0',
            '-c:s', 'copy', '-c:a', 'copy', '-c:v', 'copy',
            '-metadata', f'title={metadata}',
            '-metadata', f'author={metadata}',
            '-metadata:s:s', f'title={metadata}',
            '-metadata:s:a', f'title={metadata}',
            '-metadata:s:v', f'title={metadata}',
            '-metadata', f'artist={metadata}',
            '-movflags', '+faststart', '-fflags', '+genpts',
            output_path
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()

        if os.path.exists(output_path):
            await ms.edit("<i>Metadata Has Been Successfully Added ✅</i>")
            return output_path
        else:
            await ms.edit("<i>Failed To Add Metadata ❌</i>")
            return None
    except Exception as e:
        print(f"Error Occurred While Adding Metadata : {str(e)}")
        await ms.edit("<i>An Error Occurred While Adding Metadata ❌</i>")
        return None


# 🔥 New Helpers for Leech + Metadata Fix

async def download_file(url, filename):
    """Download file from direct link (supports Azalea-style links)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to download: {resp.status}")
            with open(filename, "wb") as f:
                while True:
                    chunk = await resp.content.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
    return filename


async def fix_metadata(input_file, output_file):
    """Fix 0:00 duration issue by remuxing with ffmpeg."""
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    command = [
        "ffmpeg", "-i", input_file,
        "-c", "copy", "-map", "0",
        "-avoid_negative_ts", "make_zero",
        "-fflags", "+genpts+discardcorrupt",
        "-y", output_file
    ]
    subprocess.run(command, check=True)
    return output_file


async def cleanup_file(path):
    """Delete temporary file safely."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"Cleanup failed: {e}")
