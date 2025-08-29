import os
import asyncio
import shutil
import subprocess
from typing import Optional, Dict, Any, List
import re

from pyrogram import Client, filters
from pyrogram.types import Message
from config import Config

LOG_CHANNEL = int(getattr(Config, "LOG_CHANNEL", os.getenv("LOG_CHANNEL", "-1002446826368")))
DOWNLOAD_DIR = "downloads/auto_rename"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ---------------- In-memory locks and sessions ----------------
PROCESSING_LOCKS: Dict[int, asyncio.Lock] = {}
_inmem_sessions: Dict[int, Dict[str, Any]] = {}

# ---------------- Helpers ----------------
def parse_filename(filename: str) -> Dict[str, Optional[str]]:
    s = None
    e = None
    q = None
    channel = None
    name = filename or ""

    # optional channel tag [@ChannelName]
    mchan = re.search(r"\[@([A-Za-z0-9_\-]+)\]", name)
    if mchan:
        channel = mchan.group(1)

    # S01E01 patterns
    m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", name)
    if m:
        s = m.group(1).zfill(2)
        e = m.group(2).zfill(2)
    else:
        m2 = re.search(r"(\d{1,2})[xX](\d{1,2})", name)
        if m2:
            s = m2.group(1).zfill(2)
            e = m2.group(2).zfill(2)
        else:
            me = re.search(r"[Ee](\d{1,2})", name)
            if me:
                e = me.group(1).zfill(2)

    # quality detection
    mq = re.search(r"(\d{3,4}p|[24]k|2K|4K)", name, flags=re.IGNORECASE)
    if mq:
        qraw = mq.group(1)
        q = qraw.lower()
        if q == "360p":
            q = "480p"

    return {"sn": s, "ep": e, "quality": q, "channel": channel}

def _user_temp_dir(user_id: int) -> str:
    d = os.path.join(DOWNLOAD_DIR, str(user_id))
    os.makedirs(d, exist_ok=True)
    return d

def normalize_quality(q: Optional[str]) -> Optional[str]:
    if not q:
        return None
    q = q.lower()
    if q == "360p":
        return "480p"
    return q

def build_new_filename(fmt: str, ep: Optional[str], sn: Optional[str], quality: Optional[str]) -> str:
    ep_val = (str(int(ep)).zfill(2)) if ep and ep.isdigit() else (ep or "")
    sn_val = (str(int(sn)).zfill(2)) if sn and sn.isdigit() else (sn or "")
    quality_val = quality or ""

    out = fmt
    if "{Sn}" in out and not sn_val:
        out = out.replace("S{Sn}", "").replace("{Sn}", "")

    out = out.replace("{ep}", ep_val)
    out = out.replace("{Sn}", sn_val)
    out = out.replace("{quality}", quality_val)
    out = re.sub(r"\s+", " ", out).strip()
    return out

async def apply_metadata_copy(src: str, dst: str, title: str, audio_title: Optional[str] = None):
    loop = asyncio.get_event_loop()
    def _run():
        cmd = [
            "ffmpeg", "-y",
            "-i", src,
            "-map", "0",
            "-c", "copy",
            "-metadata", f"title={title}"
        ]
        if audio_title:
            cmd += ["-metadata:s:a:0", f"title={audio_title}"]
        cmd += [dst]
        subprocess.run(cmd, check=True)

    try:
        await loop.run_in_executor(None, _run)
        return True
    except Exception as e:
        print("FFMPEG metadata failed:", e)
        try:
            shutil.copy(src, dst)
            return True
        except Exception as e2:
            print("Fallback copy failed:", e2)
            return False

# ---------------- Session ----------------
async def create_session(user_id: int) -> Dict[str, Any]:
    session = {
        "user_id": user_id,
        "thumbnail": None,
        "metadata": None,
        "format": None,
        "season_default": None,
        "episodes": [],
        "processing": False
    }
    _inmem_sessions[user_id] = session
    PROCESSING_LOCKS.setdefault(user_id, asyncio.Lock())
    return session

async def get_session(user_id: int) -> Optional[Dict[str, Any]]:
    return _inmem_sessions.get(user_id)

async def update_session(user_id: int, update: Dict[str, Any]):
    s = _inmem_sessions.get(user_id, {})
    s.update(update)
    _inmem_sessions[user_id] = s

async def add_episode_entry(user_id: int, entry: Dict[str, Any]):
    s = _inmem_sessions.setdefault(user_id, {"episodes": []})
    s.setdefault("episodes", []).append(entry)

async def set_processing(user_id: int, flag: bool):
    await update_session(user_id, {"processing": flag})

async def delete_session(user_id: int):
    if user_id in _inmem_sessions:
        del _inmem_sessions[user_id]

# ---------------- Handlers ----------------
@Client.on_message(filters.command("auto_rename") & filters.private)
async def cmd_auto_rename(client: Client, message: Message):
    uid = message.from_user.id
    await create_session(uid)
    await message.reply_text("📸 Send thumbnail for auto rename.")

@Client.on_message(filters.photo & filters.private)
async def auto_thumb_save(client: Client, message: Message):
    uid = message.from_user.id
    session = await get_session(uid)
    if not session:
        return
    if session.get("thumbnail"):
        await message.reply_text("✅ Thumbnail already set. /auto_rename to replace.")
        return
    temp = _user_temp_dir(uid)
    thumb_path = os.path.join(temp, "thumb.jpg")
    await message.download(file_name=thumb_path)
    await update_session(uid, {"thumbnail": thumb_path})
    await message.reply_text("✅ Thumbnail saved! Send metadata now.")

@Client.on_message(filters.text & filters.private)
async def auto_text_handler(client: Client, message: Message):
    uid = message.from_user.id
    session = await get_session(uid)
    if not session:
        return

    if not session.get("metadata"):
        await update_session(uid, {"metadata": message.text})
        await message.reply_text("✅ Metadata saved! Send rename format with {ep} {Sn} {quality}")
        return

    if not session.get("format"):
        fmt = message.text
        await update_session(uid, {"format": fmt})
        await message.reply_text("✅ Format saved! Upload files now.")
        return

    txt = message.text.strip()
    m = re.match(r"^(\d{1,3})\s*(\d{3,4}p)?$", txt, flags=re.IGNORECASE)
    if m:
        ep = m.group(1).zfill(2)
        q = m.group(2).lower() if m.group(2) else "480p"
        q = normalize_quality(q)
        entry = {"ep": ep, "sn": session.get("season_default"), "quality": q, "file_id": None, "state": "pending"}
        await add_episode_entry(uid, entry)
        await message.reply_text(f"📥 Saved Episode {ep} • {q}")

@Client.on_message(filters.private & (filters.document | filters.video))
async def auto_file_handler(client: Client, message: Message):
    uid = message.from_user.id
    session = await get_session(uid)
    if not session or not session.get("format"):
        await message.reply_text("❗ Set format first with /auto_rename")
        return

    media = getattr(message, message.media.value)
    orig_fname = getattr(media, "file_name", None) or (message.caption or f"file_{message.message_id}")

    parsed = parse_filename(orig_fname)
    sn = parsed.get("sn") or session.get("season_default")
    ep = parsed.get("ep")
    quality = normalize_quality(parsed.get("quality")) or "480p"
    file_id = media.file_id

    entry = {"ep": ep or "", "sn": sn or "", "quality": quality, "file_id": file_id, "orig_name": orig_fname, "state": "pending"}
    await add_episode_entry(uid, entry)
    display_ep = ep if ep else "Unknown"
    await message.reply_text(f"📥 Saved Episode {display_ep} • {quality}")

@Client.on_message(filters.command("rename_all") & filters.private)
async def cmd_rename_all(client: Client, message: Message):
    uid = message.from_user.id
    session = await get_session(uid)
    if not session or not session.get("episodes"):
        return await message.reply_text("❗ No active session or episodes.")

    lock = PROCESSING_LOCKS.setdefault(uid, asyncio.Lock())
    if lock.locked():
        return await message.reply_text("⚠️ Rename in progress. Wait.")

    await message.reply_text(f"🚀 Starting rename for {len(session.get('episodes', []))} items...")

    async with lock:
        await set_processing(uid, True)
        try:
            await _process_session(client, uid, message)
        finally:
            await set_processing(uid, False)
            await delete_session(uid)
            await message.reply_text("✅ All episodes renamed and uploaded successfully!")

# ---------------- Internal processing ----------------
async def _process_session(client: Client, user_id: int, trigger_message: Message):
    session = await get_session(user_id)
    if not session:
        return
    episodes = session.get("episodes", [])
    eps_map: Dict[str, List[Dict[str, Any]]] = {}
    for ep in episodes:
        epnum = ep.get("ep") or ""
        eps_map.setdefault(epnum, []).append(ep)

    ep_keys_sorted = sorted([k for k in eps_map.keys() if k and k.isdigit()], key=lambda x: int(x))
    Q_ORDER = ["480p", "720p", "1080p"]

    for epnum in ep_keys_sorted:
        for q in Q_ORDER:
            entries = [x for x in eps_map.get(epnum, []) if x.get("quality") == q and x.get("state") == "pending"]
            for entry in entries:
                try:
                    await _process_single_entry(client, user_id, session, entry, trigger_message)
                    entry["state"] = "done"
                except Exception as e:
                    print("Error processing entry:", e)
                    entry["state"] = "failed"

async def _process_single_entry(client: Client, user_id: int, session: Dict[str, Any], entry: Dict[str, Any], trigger_message: Message):
    file_id = entry.get("file_id")
    if not file_id:
        return

    tmpdir = _user_temp_dir(user_id)
    orig_name = entry.get("orig_name") or "file"
    ext = os.path.splitext(orig_name)[1] or ""
    dl_path = os.path.join(tmpdir, f"dl_{entry.get('ep')}_{entry.get('quality')}{ext}")

    await client.download_media(file_id, file_name=dl_path)

    fmt = session.get("format") or "{ep} {quality}"
    new_name = build_new_filename(fmt, entry.get("ep"), entry.get("sn"), entry.get("quality"))
    if not os.path.splitext(new_name)[1]:
        new_name = new_name + ext

    out_path = os.path.join(tmpdir, f"renamed_{new_name}")
    metadata = session.get("metadata") or ""
    succeeded = await apply_metadata_copy(dl_path, out_path, new_name, audio_title=metadata)
    if not succeeded:
        out_path = dl_path

    thumb = session.get("thumbnail")
    caption = f"**{new_name}**"
    lowext = ext.lower()
    if lowext in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
        await client.send_video(chat_id=user_id, video=out_path, thumb=thumb if thumb and os.path.exists(thumb) else None, caption=caption, supports_streaming=True)
        await client.send_video(chat_id=LOG_CHANNEL, video=out_path, thumb=thumb if thumb and os.path.exists(thumb) else None, caption=caption, supports_streaming=True)
    else:
        await client.send_document(chat_id=user_id, document=out_path, thumb=thumb if thumb and os.path.exists(thumb) else None, caption=caption)
        await client.send_document(chat_id=LOG_CHANNEL, document=out_path, thumb=thumb if thumb and os.path.exists(thumb) else None, caption=caption)

    try:
        if os.path.exists(dl_path):
            os.remove(dl_path)
        if os.path.exists(out_path) and out_path != dl_path:
            os.remove(out_path)
    except Exception:
        pass
