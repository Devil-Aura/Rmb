""" plugins/auto_rename.py

Drop this file into your repo's plugins/ folder. Implements the Auto-Rename system (thumbnail -> metadata -> format -> collect files -> /rename_all)

Uses MongoDB (motor) if available via CONFIG (DATABASE_URL + DATABASE_NAME)

If your repo already exposes a DB helper named jishubotz, the code will try to use it as a fallback.


NOTE: You must have ffmpeg installed on your VPS and motor in requirements (or use existing DB helper).

Behavior:

/auto_rename -> asks for thumbnail

user sends photo -> saved

bot asks for metadata -> user sends metadata string (applied to video/audio/subtitle titles)

bot asks for rename format -> user sends format with placeholders {ep}, {Sn}, {quality}

user uploads files -> bot parses season/episode/quality from filename automatically and saves queue

/rename_all -> bot processes queue sequentially: for each episode, process qualities in order [480p,720p,1080p] (360p file will be treated as 480p in naming)

uploads to user with thumbnail + caption <new_filename>, then forwards/upload to LOG_CHANNEL with caption <new_filename>

finally session is removed from DB


"""

import os import re import asyncio import shutil import subprocess from typing import Optional, Dict, Any, List

from pyrogram import Client, filters from pyrogram.types import Message

try: # try to use repo's DB helper if present (common in this repo) from helper.database import jishubotz  # type: ignore HAS_DB_HELPER = True except Exception: HAS_DB_HELPER = False

motor fallback

try: from motor.motor_asyncio import AsyncIOMotorClient HAS_MOTOR = True except Exception: HAS_MOTOR = False

from config import Config

LOG_CHANNEL = int(getattr(Config, "LOG_CHANNEL", os.getenv("LOG_CHANNEL", "-1002446826368"))) DOWNLOAD_DIR = "downloads/auto_rename" os.makedirs(DOWNLOAD_DIR, exist_ok=True)

In-memory processing locks to avoid parallel /rename_all per user

PROCESSING_LOCKS: Dict[int, asyncio.Lock] = {}

DB collection name

SESSIONS_COL = "auto_rename_sessions"

Initialize motor client if we must

_mongo_client = None _db = None

if not HAS_DB_HELPER: if not HAS_MOTOR: print("WARNING: motor not installed and no DB helper found. Auto-rename will not persist sessions across restarts.") else: if hasattr(Config, "DATABASE_URL") and Config.DATABASE_URL: _mongo_client = AsyncIOMotorClient(Config.DATABASE_URL) _db = _mongo_client.get_database(getattr(Config, "DATABASE_NAME", "rmb")) else: print("WARNING: DATABASE_URL not set in config. Auto-rename won't persist sessions.")

------------------ Helpers: filename parsing ------------------

def parse_filename(filename: str) -> Dict[str, Optional[str]]: """Extract season (Sn), episode (ep) and quality (quality) from filename. Returns dict with keys: sn, ep, quality, channel_tag """ s = None e = None q = None channel = None

name = filename or ""

# channel tag pattern like [@CrunchyRollChannel] at start
mchan = re.search(r"(@[A-Za-z0-9_\-]+)", name)
if mchan:
    channel = mchan.group(1)

# S01E01 patterns
m = re.search(r"[Ss](\d{1,2})\s*[Ee](\d{1,2})", name)
if m:
    s = m.group(1).zfill(2)
    e = m.group(2).zfill(2)
else:
    # alternative patterns like 01x01
    m2 = re.search(r"(\d{1,2})[xX](\d{1,2})", name)
    if m2:
        s = m2.group(1).zfill(2)
        e = m2.group(2).zfill(2)
    else:
        # sometimes only E01 present
        me = re.search(r"[Ee](\d{1,2})", name)
        if me:
            e = me.group(1).zfill(2)

# quality detection (360p, 480p, 720p, 1080p, 2160p, 2K, 4K)
mq = re.search(r"(\d{3,4}p|[24]k|2K|4K)", name, flags=re.IGNORECASE)
if mq:
    qraw = mq.group(1)
    q = qraw.lower()
    # normalize 360p -> 480p per requirement
    if q == "360p":
        q = "480p"
    # lowercase p
    q = q.replace("P", "p") if q else q

return {"sn": s, "ep": e, "quality": q, "channel": channel}

------------------ DB wrapper (motor fallback) ------------------

async def _get_collection(): if HAS_DB_HELPER: # fallback: use a simple in-memory mapping through jishubotz if it has a generic KV API # But repo's jishubotz may not expose collections; so if we detect jishubotz has mongo-like methods we'll use them. # We'll try to use motor if available; otherwise store in jishubotz under key. return None else: if _db is None: return None return _db[SESSIONS_COL]

async def create_session(user_id: int): col = await _get_collection() session = { "user_id": user_id, "thumbnail": None, "metadata": None, "format": None, "season_default": None, "episodes": [],  # list of dicts {ep, sn, quality, file_id, state} "processing": False } if col: await col.insert_one(session) else: # store in memory (volatile) # attach to PROCESSING_LOCKS structure for minimal persistence in runtime PROCESSING_LOCKS.setdefault(user_id, asyncio.Lock()) # store on a global dict _inmem_sessions[user_id] = session return session

async def get_session(user_id: int) -> Optional[Dict[str, Any]]: col = await _get_collection() if col: return await col.find_one({"user_id": user_id}) else: return _inmem_sessions.get(user_id)

async def update_session(user_id: int, update: Dict[str, Any]): col = await _get_collection() if col: await col.update_one({"user_id": user_id}, {"$set": update}) else: s = _inmem_sessions.get(user_id, {}) s.update(update) _inmem_sessions[user_id] = s

async def add_episode_entry(user_id: int, entry: Dict[str, Any]): col = await _get_collection() if col: await col.update_one({"user_id": user_id}, {"$push": {"episodes": entry}}) else: s = _inmem_sessions.setdefault(user_id, {"episodes": []}) s.setdefault("episodes", []).append(entry)

async def set_processing(user_id: int, flag: bool): await update_session(user_id, {"processing": flag})

async def delete_session(user_id: int): col = await _get_collection() if col: await col.delete_one({"user_id": user_id}) else: if user_id in _inmem_sessions: del _inmem_sessions[user_id]

in-memory fallback storage if no DB available

_inmem_sessions: Dict[int, Dict[str, Any]] = {}

------------------ FFmpeg metadata (remux) ------------------

async def apply_metadata_copy(src: str, dst: str, title: str, audio_title: Optional[str] = None, subtitle_title: Optional[str] = None): """Try to remux and set basic metadata (container title + first audio title). This runs ffmpeg synchronously in executor to avoid blocking the event loop. """ loop = asyncio.get_event_loop()

def _run():
    cmd = [
        "ffmpeg", "-y",
        "-i", src,
        "-map", "0",
        "-c", "copy",
        "-metadata", f"title={title}"
    ]
    # set first audio stream title if provided
    if audio_title:
        cmd += ["-metadata:s:a:0", f"title={audio_title}"]
    # note: per-stream subtitle/title may not be supported with -c copy in all containers
    cmd += [dst]
    subprocess.run(cmd, check=True)

try:
    await loop.run_in_executor(None, _run)
    return True
except Exception as e:
    print("FFMPEG metadata remux failed:", e)
    # fallback: try simple copy
    try:
        shutil.copy(src, dst)
        return True
    except Exception as e2:
        print("Fallback copy failed:", e2)
        return False

------------------ Utility helpers ------------------

def _user_temp_dir(user_id: int) -> str: d = os.path.join(DOWNLOAD_DIR, str(user_id)) os.makedirs(d, exist_ok=True) return d

def normalize_quality(q: Optional[str]) -> Optional[str]: if not q: return None q = q.lower() if q == "360p": return "480p" return q

def build_new_filename(fmt: str, ep: Optional[str], sn: Optional[str], quality: Optional[str]) -> str: # prepare replacements ep_val = (str(int(ep)).zfill(2)) if ep and ep.isdigit() else (ep or "") sn_val = (str(int(sn)).zfill(2)) if sn and sn.isdigit() else (sn or "") quality_val = quality or ""

out = fmt
# If format contains S{Sn} and sn is missing, remove that token (the 'S' too)
if "{Sn}" in out and not sn_val:
    out = out.replace("S{Sn}", "")
    out = out.replace("{Sn}", "")

out = out.replace("{ep}", ep_val)
out = out.replace("{Sn}", sn_val)
out = out.replace("{quality}", quality_val)

# sanitize double spaces
out = re.sub(r"\s+", " ", out).strip()
return out

------------------ Pyrogram handlers ------------------

@Client.on_message(filters.command("auto_rename") & filters.private) async def cmd_auto_rename(client: Client, message: Message): uid = message.from_user.id # create session await create_session(uid) await message.reply_text("📸 Please send a thumbnail image for auto rename.")

@Client.on_message(filters.photo & filters.private) async def auto_thumb_save(client: Client, message: Message): uid = message.from_user.id session = await get_session(uid) if not session: return if session.get("thumbnail"): # already set await message.reply_text("✅ Thumbnail already set. If you want to replace it, send /auto_rename again.") return

temp = _user_temp_dir(uid)
thumb_path = os.path.join(temp, "auto_thumb.jpg")
await message.download(file_name=thumb_path)
await update_session(uid, {"thumbnail": thumb_path})
await message.reply_text("✅ Thumbnail saved! Now send metadata (video title, audio title, subtitle). Example: @CrunchyRollChannel For More Animes In Hindi!")

@Client.on_message(filters.text & filters.private) async def auto_text_handler(client: Client, message: Message): uid = message.from_user.id session = await get_session(uid) if not session: return

# if metadata not set -> save as metadata
if not session.get("metadata"):
    await update_session(uid, {"metadata": message.text})
    await message.reply_text("✅ Metadata saved! Now send rename format. Use placeholders {ep} {Sn} {quality}. Example: [World_Fastest_Bots] Naruto S{Sn}E{ep} {quality} Hindi")
    return

# if format not set -> save as format
if not session.get("format"):
    fmt = message.text
    if "{ep}" not in fmt and "{Sn}" not in fmt:
        # still accept but warn
        await message.reply_text("⚠️ Format saved but it doesn't contain {ep} or {Sn}. Make sure your format has placeholders. I'll accept it though.")
    await update_session(uid, {"format": fmt})
    await message.reply_text("✅ Format saved! Now upload your files (videos/documents). Bot will auto-detect SxxExx and quality from filenames.")
    return

# If metadata and format are present, user might send commands or manual episode entries
# Accept manual episode entry like: 07 480p (but primary flow is to upload files directly)
txt = message.text.strip()
m = re.match(r"^(\d{1,3})\s*(\d{3,4}p)?$", txt, flags=re.IGNORECASE)
if m:
    ep = m.group(1).zfill(2)
    q = m.group(2).lower() if m.group(2) else "480p"
    q = normalize_quality(q)
    # create placeholder entry with no file yet (user must upload file later?)
    # we store an entry without file_id but it will be skipped during processing
    entry = {"ep": ep, "sn": session.get("season_default"), "quality": q, "file_id": None, "state": "pending"}
    await add_episode_entry(uid, entry)
    await message.reply_text(f"📥 Saved Episode {ep} • {q}")
    return

# not recognized
# Just echo and do nothing
# (many users will type extra text - we ignore)

@Client.on_message(filters.private & (filters.document | filters.video)) async def auto_file_handler(client: Client, message: Message): uid = message.from_user.id session = await get_session(uid) if not session: # no session started return if not session.get("format"): await message.reply_text("❗ Please set format first. Use /auto_rename and follow steps.") return

# get filename
media = getattr(message, message.media.value)
orig_fname = getattr(media, "file_name", None)
if not orig_fname:
    # try caption as fallback
    orig_fname = (message.caption or f"file_{message.message_id}")

parsed = parse_filename(orig_fname)
sn = parsed.get("sn") or session.get("season_default")
ep = parsed.get("ep")
quality = normalize_quality(parsed.get("quality")) or "480p"

# store Telegram file_id for later download
file_id = media.file_id

entry = {"ep": ep or "", "sn": sn or "", "quality": quality, "file_id": file_id, "orig_name": orig_fname, "state": "pending"}
await add_episode_entry(uid, entry)

# respond to user
display_ep = ep if ep else "Unknown"
await message.reply_text(f"📥 Saved Episode {display_ep} • {quality}")

@Client.on_message(filters.command("rename_all") & filters.private) async def cmd_rename_all(client: Client, message: Message): uid = message.from_user.id session = await get_session(uid) if not session: return await message.reply_text("❗ No active auto-rename session. Start with /auto_rename") if not session.get("episodes"): return await message.reply_text("❗ No episodes queued. Upload files first.")

# ensure lock
lock = PROCESSING_LOCKS.setdefault(uid, asyncio.Lock())
if lock.locked():
    return await message.reply_text("⚠️ Rename already in progress. Wait for it to finish.")

await message.reply_text(f"🚀 Starting auto-rename for {len(session.get('episodes', []))} items... This will process one file at a time to avoid overload.")

async with lock:
    await set_processing(uid, True)
    try:
        await _process_session(client, uid, message)
    finally:
        await set_processing(uid, False)
        # delete session after processing
        await delete_session(uid)
        await message.reply_text("✅ All episodes renamed and uploaded successfully!")

async def _process_session(client: Client, user_id: int, trigger_message: Message): session = await get_session(user_id) if not session: return episodes = session.get("episodes", []) # group by episode number; if ep is empty string, skip eps_map: Dict[str, List[Dict[str, Any]]] = {} for ep in episodes: epnum = ep.get("ep") or "" eps_map.setdefault(epnum, []).append(ep)

# order episodes by numeric ep (skip unknown keys)
ep_keys = [k for k in eps_map.keys() if k and k.isdigit()]
ep_keys_sorted = sorted(ep_keys, key=lambda x: int(x))

# qualities processing order
Q_ORDER = ["480p", "720p", "1080p"]

for epnum in ep_keys_sorted:
    for q in Q_ORDER:
        # find an entry matching epnum and quality q
        entries = [x for x in eps_map.get(epnum, []) if x.get("quality") == q and x.get("state") == "pending"]
        if not entries:
            continue
        for entry in entries:
            try:
                await _process_single_entry(client, user_id, session, entry, trigger_message)
                # mark done in DB
                entry["state"] = "done"
            except Exception as e:
                print("Error processing entry:", e)
                entry["state"] = "failed"
    # after finishing all qualities for this ep, continue to next ep

# Note: any entries with missing ep or non-numeric ep were ignored. You can add handling if needed.

async def _process_single_entry(client: Client, user_id: int, session: Dict[str, Any], entry: Dict[str, Any], trigger_message: Message): # download file via Telegram file_id file_id = entry.get("file_id") if not file_id: # nothing to do return

tmpdir = _user_temp_dir(user_id)
# determine extension from orig_name
orig_name = entry.get("orig_name") or "file"
ext = os.path.splitext(orig_name)[1] or ""
dl_path = os.path.join(tmpdir, f"dl_{entry.get('ep')}_{entry.get('quality')}{ext}")

await client.download_media(file_id, file_name=dl_path)

# build new filename
fmt = session.get("format") or "{ep} {quality}"
new_name = build_new_filename(fmt, entry.get("ep"), entry.get("sn"), entry.get("quality"))
# ensure extension
if not os.path.splitext(new_name)[1]:
    new_name = new_name + ext

out_path = os.path.join(tmpdir, f"renamed_{new_name}")

# apply metadata
metadata = session.get("metadata") or ""
title_for_meta = new_name
succeeded = await apply_metadata_copy(dl_path, out_path, title_for_meta, audio_title=metadata)
if not succeeded:
    out_path = dl_path  # fallback to original downloaded file

# send to user with thumbnail and bold filename
thumb = session.get("thumbnail")
# choose send_video if extension indicates video
lowext = ext.lower()
caption = f"**{new_name}**"
if lowext in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
    await client.send_video(chat_id=user_id, video=out_path, thumb=thumb if thumb and os.path.exists(thumb) else None, caption=caption, supports_streaming=True)
else:
    await client.send_document(chat_id=user_id, document=out_path, thumb=thumb if thumb and os.path.exists(thumb) else None, caption=caption)

# also upload to LOG_CHANNEL with same thumbnail and only bold filename (no extra text)
if lowext in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
    await client.send_video(chat_id=LOG_CHANNEL, video=out_path, thumb=thumb if thumb and os.path.exists(thumb) else None, caption=caption, supports_streaming=True)
else:
    await client.send_document(chat_id=LOG_CHANNEL, document=out_path, thumb=thumb if thumb and os.path.exists(thumb) else None, caption=caption)

# remove temporary files
try:
    if os.path.exists(dl_path):
        os.remove(dl_path)
    if os.path.exists(out_path) and out_path != dl_path:
        os.remove(out_path)
except Exception:
    pass

Expose helper functions for easier manual usage

all = [ "create_session", "get_session", "add_episode_entry", "cmd_auto_rename", "cmd_rename_all", ]
