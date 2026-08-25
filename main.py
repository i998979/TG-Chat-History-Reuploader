import argparse
import asyncio
import glob
import hashlib
import html
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote

import cryptg
import cv2
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telethon import TelegramClient, errors, functions, types, utils, helpers

load_dotenv()

API_ID = None
API_HASH = None
SESSION_NAME = None

TARGET_CHANNEL = None
HTML_FILE = None

SEVEN_ZIP_PATH = r"C:\Program Files\7-Zip\7z.exe"
ARCHIVE_PASSWORD = ""

PROGRESS_FILE = "tg_upload_progress.json"
REGISTRY_FILE = "tg_upload_registry.json"
REPACK_STATE_FILE = "repack_state.json"
LOG_FILE = "upload_log.txt"

MAX_CAPTION_LENGTH = 1000
MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2 GB Limit
IS_PREMIUM = False
DELETE_FILE = False

logger_initialized = False


class Logger:
    def __init__(self, terminal, log_file):
        self.terminal = terminal
        self.log_file = log_file

    def write(self, message):
        self.terminal.write(message)
        if message.startswith('\r') and '\n' not in message: return
        self.log_file.write(message.replace('\r', ''))
        self.log_file.flush()

    def flush(self):
        self.terminal.flush()
        self.log_file.flush()


def init_logger():
    global logger_initialized
    if logger_initialized: return
    log_file = open(LOG_FILE, "a", encoding="utf-8")
    sys.stdout = Logger(sys.stdout, log_file)
    sys.stderr = Logger(sys.stderr, log_file)
    logger_initialized = True


def set_args(**kwargs):
    global API_ID, API_HASH, SESSION_NAME, TARGET_CHANNEL, HTML_FILE
    global SEVEN_ZIP_PATH, ARCHIVE_PASSWORD, PROGRESS_FILE, REGISTRY_FILE
    global REPACK_STATE_FILE, LOG_FILE, MAX_CAPTION_LENGTH, MAX_FILE_SIZE, IS_PREMIUM
    global DELETE_FILE

    API_ID = kwargs.get("api_id") or os.environ.get("API_ID")
    if API_ID is not None:
        API_ID = int(API_ID)
    API_HASH = kwargs.get("api_hash") or os.environ.get("API_HASH")

    SESSION_NAME = kwargs.get("session_name")
    TARGET_CHANNEL = kwargs.get("target_channel") or os.environ.get("TARGET_CHANNEL")
    if TARGET_CHANNEL is not None:
        TARGET_CHANNEL = int(TARGET_CHANNEL)

    html_file = kwargs.get("html_file") or os.environ.get("HTML_FILE")
    if html_file:
        HTML_FILE = html_file.strip("\"'")

    SEVEN_ZIP_PATH = kwargs.get("seven_zip_path")
    ARCHIVE_PASSWORD = kwargs.get("archive_password") or os.environ.get("ARCHIVE_PASSWORD") or ""

    PROGRESS_FILE = kwargs.get("progress_file", "tg_upload_progress.json")
    REGISTRY_FILE = kwargs.get("registry_file", "tg_upload_registry.json")
    REPACK_STATE_FILE = kwargs.get("repack_state_file", "repack_state.json")
    LOG_FILE = kwargs.get("log_file", "upload_log.txt")
    MAX_CAPTION_LENGTH = kwargs.get("max_caption_length", 1000)

    DELETE_FILE = kwargs.get("delete_file", False)

    IS_PREMIUM = kwargs.get("is_premium", False)
    if IS_PREMIUM:
        MAX_FILE_SIZE = 4000 * 1024 * 1024  # 4 GB Limit for Telegram Premium
    else:
        MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2 GB Limit for Standard Users

    # Validation
    missing_vars = []
    if API_ID is None: missing_vars.append("API_ID")
    if not API_HASH: missing_vars.append("API_HASH")
    if not SESSION_NAME: missing_vars.append("SESSION_NAME")
    if TARGET_CHANNEL is None: missing_vars.append("TARGET_CHANNEL")
    if not HTML_FILE: missing_vars.append("HTML_FILE")

    if missing_vars:
        raise ValueError(f"Missing required configuration variables: {', '.join(missing_vars)}")


# Archive repack
def load_repack_state():
    """
    Loads the state of archive repacking from a JSON file.
    This helps the script remember which archives were already extracted
    or zipped in case of a crash or restart.

    :return: Dictionary representing the repack state.
    """
    if os.path.exists(REPACK_STATE_FILE):
        try:
            with open(REPACK_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_repack_state(state):
    """
    Saves the current state of archive repacking to a JSON file.

    :param state: Dictionary of the current repack state.
    """
    with open(REPACK_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=4)


def load_progress(target_key):
    """
    Loads the current upload progress (album index) for a specific chat.

    :param target_key: Name of the chat/channel.
    :return: Integer index of the last processed album.
    """
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r') as f:
                return json.load(f).get(target_key, 0)
        except:
            pass
    return 0


def save_progress(target_key, idx):
    """
    Saves the current upload progress (album index) to a JSON file.

    :param target_key: Name of the chat/channel.
    :param idx: The integer index to save.
    """
    data = {}
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r') as f:
                data = json.load(f)
        except:
            pass
    data[target_key] = idx
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(data, f, indent=4)


# Media registry
def load_registry(chat_name):
    """
    Loads the registry mapping of uploaded files to their Telegram Cloud Message IDs.
    This prevents re-uploading the same files.

    :param chat_name: Name of the chat/channel.
    :return: Dictionary mapping file identifiers to Cloud Message IDs.
    """
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, 'r') as f:
                return json.load(f).get(chat_name, {})
        except:
            pass
    return {}


def save_registry(chat_name, new_entries):
    """
    Updates and saves the media registry with new entries to disk.

    :param chat_name: Name of the chat/channel.
    :param new_entries: Dictionary of new mappings to add.
    """
    data = {}
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, 'r') as f:
                data = json.load(f)
        except:
            pass
    chat_data = data.get(chat_name, {})
    chat_data.update(new_entries)
    data[chat_name] = chat_data
    with open(REGISTRY_FILE, 'w') as f:
        json.dump(data, f, indent=4)


def parse_size_to_bytes(size_str):
    """
    Parses file size to bytes.

    :param size_str: String file size and unit.
    :return: Size in bytes, or 0 if unparseable.
    """
    match = re.search(r'([\d.]+)\s*(KB|MB|GB|Bytes|B)', size_str, re.IGNORECASE)
    if match:
        val = float(match.group(1))
        unit = match.group(2).upper()
        if unit == 'KB':
            return val * 1024
        elif unit == 'MB':
            return val * 1024 ** 2
        elif unit == 'GB':
            return val * 1024 ** 3
        return val
    return 0


def parse_duration_to_seconds(dur_str):
    """
    Parses duration string (e.g., "01:23:45" or "12:34") to total seconds.

    :param dur_str: Duration in HH:MM:SS or MM:SS format.
    :return: Integer duration in seconds, or 0 if unparseable.
    """
    match = re.search(r'\b(?:(\d+):)?(\d+):(\d{2})\b', dur_str)
    if match:
        groups = match.groups()
        if groups[0]:
            return int(groups[0]) * 3600 + int(groups[1]) * 60 + int(groups[2])
        else:
            return int(groups[1]) * 60 + int(groups[2])
    return 0


def get_video_duration(path):
    """
    Uses FFprobe to extract the exact duration of a video file in seconds.

    :param path: The file path to the video.
    :return: Float duration of the video in seconds. Returns 0 on failure.
    """
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of',
               'default=noprint_wrappers=1:nokey=1', path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return float(result.stdout.strip())
    except:
        return 0


def split_video(file_path):
    """
    Splits a video file using FFmpeg into smaller chunks if it exceeds the MAX_FILE_SIZE.
    Calculates the required number of chunks and uses segment multiplexing.

    :param file_path: Path to the large video file.
    :return: A sorted list of file paths corresponding to the split video chunks.
    """
    file_size = os.path.getsize(file_path)
    if file_size <= MAX_FILE_SIZE: return [file_path]

    print(f"  ✂️ File is {file_size / (1024 ** 3):.2f}GB. Splitting into chunks...")
    duration = get_video_duration(file_path)
    if duration == 0:
        print("  ❌ Could not determine duration for splitting. Skipping split.")
        return [file_path]

    num_chunks = math.ceil(file_size / MAX_FILE_SIZE)
    chunk_duration = duration / num_chunks
    base_name, ext = os.path.splitext(file_path)
    output_pattern = f"{base_name}_part_%03d{ext}"

    cmd = ['ffmpeg', '-i', file_path, '-f', 'segment', '-segment_time', str(chunk_duration), '-reset_timestamps', '1',
           '-c', 'copy', output_pattern]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(f"{base_name}_part_*[0-9]{ext}"))


async def run_7z_async(*args):
    """
    Asynchronously runs a 7-Zip subprocess command.

    :param args: The command arguments to pass to the asyncio subprocess.
    :return: The return code of the 7-Zip process.
    """
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                   stderr=asyncio.subprocess.DEVNULL)
    await process.wait()
    return process.returncode


async def repack_large_archive(archive_path, base_dir):
    """
    Extracts a large archive that exceeds Telegram limits and repacks it into
    sliced volumes (e.g., .zip.001, .zip.002) that fit within the API size limits.

    :param archive_path: Path to the large archive file.
    :param base_dir: The base directory where temporary extraction and repacking occur.
    :return: A list of generated archive slice file paths.
    """
    if not os.path.exists(SEVEN_ZIP_PATH):
        print(f"  ❌ 7-Zip executable not found at {SEVEN_ZIP_PATH}. Cannot repack.")
        return []

    state = load_repack_state()
    archive_name = os.path.basename(archive_path)
    archive_state = state.setdefault(archive_name, {
        "extracted": False,
        "zipped": False
    })

    base_name = os.path.splitext(archive_name)[0]
    temp_extract_dir = os.path.join(base_dir, "temp_extract_" + base_name)
    zip_out_base = os.path.join(base_dir, base_name + "_repacked.zip")

    if archive_state["extracted"] and not archive_state["zipped"] and not os.path.exists(temp_extract_dir):
        archive_state["extracted"] = False
    if archive_state["zipped"] and not glob.glob(f"{zip_out_base}.*"):
        archive_state["zipped"] = False

    if not archive_state["extracted"]:
        print(f"  -> Extracting {archive_name}...")
        if os.path.exists(temp_extract_dir): shutil.rmtree(temp_extract_dir, ignore_errors=True)
        os.makedirs(temp_extract_dir, exist_ok=True)

        cmd_ext = [SEVEN_ZIP_PATH, 'x', f'-o{temp_extract_dir}', '-y']
        if ARCHIVE_PASSWORD: cmd_ext.insert(2, f'-p{ARCHIVE_PASSWORD}')
        cmd_ext.append(archive_path)

        ret = await run_7z_async(*cmd_ext)
        if ret == 0:
            archive_state["extracted"] = True
            save_repack_state(state)
        else:
            print(f"  ❌ Failed to extract {archive_path}.")
            return []
    else:
        print(f"  -> {archive_name} already extracted, skipping...")

    if not archive_state["zipped"]:
        slice_size_mb = 3900 if IS_PREMIUM else 1900
        print(f"  -> Repacking {archive_name} into {slice_size_mb}m slices...")
        for p_zip in glob.glob(f"{zip_out_base}.*"):
            try:
                os.remove(p_zip)
            except OSError:
                pass

        cmd_zip = [SEVEN_ZIP_PATH, 'a', '-tzip', f'-v{slice_size_mb}m', zip_out_base,
                   os.path.join(temp_extract_dir, "*")]
        ret = await run_7z_async(*cmd_zip)

        if ret == 0:
            archive_state["zipped"] = True
            save_repack_state(state)
        else:
            print(f"  ❌ Failed to repack {archive_path}.")
            return []
    else:
        print(f"  -> {archive_name} already repacked, skipping...")

    if archive_state["zipped"]:
        shutil.rmtree(temp_extract_dir, ignore_errors=True)

    return [s for s in sorted(glob.glob(f"{zip_out_base}.*")) if not s.endswith('.deleted')]


async def fast_upload(client, file_path, file_name, progress_callback):
    """
    Uploads a file to Telegram by splitting it into parts and dispatching
    concurrent part upload requests. Handles flood wait errors gracefully.

    :param client: The active Telethon TelegramClient instance.
    :param file_path: Path to the local file to upload.
    :param file_name: Name of the file as it will appear on Telegram.
    :param progress_callback: Function to call to visually update upload progress.
    :return: A Telethon InputFile or InputFileBig object representing the uploaded file.
    """
    file_size = os.path.getsize(file_path)
    part_size = utils.get_appropriated_part_size(file_size) * 1024
    part_count = math.ceil(file_size / part_size)
    is_big = file_size > 10 * 1024 * 1024
    file_id = helpers.generate_random_long()

    md5_checksum = ""
    if not is_big:
        with open(file_path, 'rb') as f:
            md5 = hashlib.md5()
            for chunk in iter(lambda: f.read(1024 * 1024), b""): md5.update(chunk)
            md5_checksum = md5.hexdigest()

    semaphore = asyncio.Semaphore(32)
    parts_completed = 0

    async def upload_part(part_index):
        nonlocal parts_completed
        expected_chunk_size = part_size if part_index < part_count - 1 else file_size - (part_index * part_size)

        def read_chunk():
            with open(file_path, 'rb') as f:
                f.seek(part_index * part_size)
                return f.read(expected_chunk_size)

        async with semaphore:
            chunk = await asyncio.to_thread(read_chunk)
            for attempt in range(10):
                try:
                    request = functions.upload.SaveBigFilePartRequest(file_id, part_index, part_count, chunk) if is_big \
                        else functions.upload.SaveFilePartRequest(file_id, part_index, chunk)
                    await client(request)
                    parts_completed += 1
                    if progress_callback:
                        progress_callback(min(parts_completed * part_size, file_size), file_size, file_name)
                    return
                except errors.FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                except Exception:
                    await asyncio.sleep(2)

    await asyncio.gather(*[asyncio.create_task(upload_part(i)) for i in range(part_count)])
    return types.InputFileBig(file_id, part_count, file_name) if is_big else types.InputFile(file_id, part_count,
                                                                                             file_name,
                                                                                             md5_checksum=md5_checksum)


def show_progress(current, total, file_name):
    """
    Progress callback for file uploads to display MB uploaded and percentage.

    :param current: Bytes currently uploaded.
    :param total: Total bytes to upload.
    :param file_name: The name of the file being uploaded.
    """
    if not total: return
    percent = (current / total) * 100
    sys.stdout.write(
        f"\r      [Upload] {file_name}: {current / (1024 ** 2):.2f}/{total / (1024 ** 2):.2f}MB ({percent:.1f}%)")
    sys.stdout.flush()


def get_chat_name(html_path):
    """
    Extracts the name of the Telegram chat/channel from the exported HTML file.

    :param html_path: Path to the exported HTML file.
    :return: String name of the chat, or the folder name as fallback.
    """
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')
            header = soup.find('div', class_='page_header')
            if header:
                title_div = header.find('div', class_=lambda x: x and 'text' in x.split() and 'bold' in x.split())
                if title_div: return title_div.get_text(strip=True)
            if soup.title and soup.title.string:
                return soup.title.string.replace(' - Telegram', '').strip()
    except:
        pass
    return os.path.basename(os.path.dirname(html_path))


def check_folder_stats():
    """
    Scans the export folder hierarchy to calculate the total size and count of
    valid media files (ignoring deleted files or thumbnails) to detect
    ongoing downloads from Telegram.

    :return: A tuple of (valid_files_count, total_size_in_bytes).
    """
    total_size = 0
    valid_files_count = 0
    base_dir = os.path.dirname(HTML_FILE)
    potential_folders = ['video_files', 'photos', 'files', 'voice_messages', 'round_video_messages', 'stickers',
                         'documents']
    for folder_name in potential_folders:
        d = os.path.join(base_dir, folder_name)
        if not os.path.exists(d): continue
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if os.path.isfile(p) and ".deleted" not in f.lower():
                total_size += os.path.getsize(p)
                if "_thumb" not in f.lower() and "_thumbnail" not in f.lower():
                    valid_files_count += 1
    return valid_files_count, total_size


def extract_video_info(video_path):
    """
    Uses OpenCV to analyze a video file and extract its attributes:
    width, height, duration, and generates a thumbnail.

    :param video_path: Path to the local video file.
    :return: A tuple (thumbnail_path, duration_seconds, width, height).
    """
    try:
        cap = cv2.VideoCapture(video_path)
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        dur = int(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) / fps) if fps > 0 else 1

        cap.set(cv2.CAP_PROP_POS_FRAMES, 10)
        ret, frame = cap.read()
        tp = None
        if ret and frame is not None:
            tp = video_path + "_thumb.jpg"
            success, buffer = cv2.imencode(".jpg", frame)
            if success:
                with open(tp, "wb") as f:
                    f.write(buffer.tobytes())
            else:
                tp = None
        cap.release()
        if tp and not os.path.exists(tp): tp = None
        return tp, dur, w, h
    except:
        return None, 1, 512, 512


def get_all_html_files(base_html_path):
    """
    Finds all paginated HTML files in the export folder associated with the
    base export name (e.g., messages.html, messages2.html, ...).

    :param base_html_path: The initial HTML file path (usually messages.html).
    :return: A list of sorted HTML file paths based on their numeric sequence.
    """
    if not os.path.exists(base_html_path): return []
    dir_n = os.path.dirname(base_html_path)
    base_n = os.path.splitext(os.path.basename(base_html_path))[0]
    files = glob.glob(os.path.join(dir_n, f"{base_n}*.html"))
    files.sort(key=lambda x: int(re.search(r'(\d+)', os.path.basename(x)).group(1)) if re.search(r'(\d+)',
                                                                                                 os.path.basename(
                                                                                                     x)) else 1)
    return files


def extract_html_text(t_div):
    """
    Recursively extracts and reconstructs standard HTML markup like <b>, <i>, <a>
    from <div> element, translating Telegram Desktop export formatting
    back into stringified HTML suited for Telethon/Telegram bot parsing.

    :param t_div: Tag object representing the message text div.
    :return: String containing the cleaned and formatted HTML message text.
    """
    if not t_div: return ""

    def clean_element(el):
        if isinstance(el, str): return html.escape(str(el))
        tag = getattr(el, 'name', None)
        if not tag: return ""
        content = "".join(clean_element(c) for c in el.contents)

        if tag == "br":
            return "\n"
        elif tag in ["b", "strong"]:
            return f"<b>{content}</b>"
        elif tag in ["i", "em"]:
            return f"<i>{content}</i>"
        elif tag in ["u"]:
            return f"<u>{content}</u>"
        elif tag in ["s", "strike", "del"]:
            return f"<s>{content}</s>"
        elif tag in ["code"]:
            return f"<code>{content}</code>"
        elif tag == "pre":
            return f"<pre>{content}</pre>"
        elif tag == "a":
            href = html.escape(el.get("href", ""))
            return f'<a href="{href}">{content}</a>' if href else content
        elif tag == "blockquote":
            return f"<blockquote>{content}</blockquote>"
        else:
            return content

    return "".join(clean_element(c) for c in t_div.contents).strip()


def parse_messages(html_paths):
    """
    Iterates through the provided HTML files to parse out messages, media files,
    captions, and albums.
    Groups media items logically together up to 10 per album.

    :param html_paths: List of string file paths for the exported HTML pages.
    :return: A list of tuples, where each tuple is (album_items_list, captions_list).
    """
    albums, cur_alb, cur_cap_list = [], [], []
    current_sender = None

    def flush():
        nonlocal cur_alb, cur_cap_list
        if cur_alb or cur_cap_list:
            albums.append((cur_alb, cur_cap_list))
            cur_alb, cur_cap_list = [], []

    for path in html_paths:
        with open(path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')

        for msg in soup.find_all('div', class_=lambda x: x and 'message' in x.split()):
            if 'service' in msg.get('class', []): continue

            sender = None
            from_name_div = msg.find('div', class_='from_name')
            if from_name_div:
                sender = from_name_div.get_text(strip=True)
            elif 'out' in msg.get('class', []):
                sender = "Me"

            is_joined = 'joined' in msg.get('class', [])
            if not is_joined: flush()
            if not is_joined and sender: current_sender = sender

            t_div = msg.find('div', class_='text')
            msg_text = extract_html_text(t_div) if t_div else ""

            reply_markup = msg.find('div', class_='reply_markup')
            buttons_text = ""
            if reply_markup:
                kb_rows = reply_markup.find_all('div', class_='keyboard_row')
                btn_lines = []
                for row in kb_rows:
                    btns = row.find_all('a')
                    row_str = " | ".join(
                        [f"<a href=\"{html.escape(b.get('href', ''))}\">🔘 {html.escape(b.get_text(strip=True))}</a>" for
                         b in btns])
                    if row_str: btn_lines.append(row_str)
                if btn_lines: buttons_text = "\n\n" + "\n".join(btn_lines)

            full_text = msg_text + buttons_text
            if full_text: cur_cap_list.append(full_text)

            m_wrap = msg.find('div', class_='media_wrap')
            if m_wrap:
                a_tag = m_wrap.find('a', href=True)
                if a_tag and not a_tag['href'].startswith('http'):
                    href = a_tag['href']
                    filename = unquote(os.path.basename(href))
                    exact_path = os.path.normpath(os.path.join(os.path.dirname(path), unquote(href)))
                    folder_path = os.path.dirname(exact_path)

                    meta_text = m_wrap.get_text(separator=' ', strip=True)
                    expected_size = parse_size_to_bytes(meta_text)
                    expected_duration = parse_duration_to_seconds(meta_text)

                    if a_tag.has_attr('title'):
                        if expected_size == 0: expected_size = parse_size_to_bytes(a_tag['title'])
                        if expected_duration == 0: expected_duration = parse_duration_to_seconds(a_tag['title'])

                    classes = a_tag.get('class', [])
                    media_node = m_wrap.find(class_=lambda c: c and any(
                        k in c for k in ['media', 'video', 'photo', 'animated', 'round', 'voice']))
                    if media_node: classes.extend(media_node.get('class', []))

                    m_type = None
                    if any(
                            c in classes for c in
                            ['media_video', 'video_file_wrap', 'animated_wrap', 'round_video_wrap']):
                        m_type = 'video'
                    elif any(c in classes for c in ['media_photo', 'photo_wrap']):
                        m_type = 'photo'

                    if not m_type:
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in ['.mp4', '.mov', '.avi', '.webm']:
                            m_type = 'video'
                        elif ext in ['.jpg', '.jpeg', '.png']:
                            m_type = 'photo'
                        else:
                            m_type = 'file'

                    cur_alb.append({
                        'type': m_type,
                        'filename': filename,
                        'exact_path': exact_path,
                        'folder': folder_path,
                        'expected_size': expected_size,
                        'expected_duration': expected_duration
                    })
            if len(cur_alb) == 10: flush()
    flush()
    return albums


async def build_input_media(client, path, media_type):
    """
    Uploads the file and packages it into the appropriate Telegram InputMedia
    object (Video, Photo, or Document) along with its metadata (duration, dims, etc).

    :param client: Telethon TelegramClient instance.
    :param path: Local path of the file to be uploaded.
    :param media_type: String specifying 'video', 'photo', or other.
    :return: Telethon InputMediaUploaded* object ready for sending.
    """
    fname = os.path.basename(path)
    handle = await fast_upload(client, path, fname, show_progress)
    print()

    if media_type == 'video':
        tp, dur, w, h = await asyncio.to_thread(extract_video_info, path)
        t_file = await client.upload_file(tp) if tp else None
        return types.InputMediaUploadedDocument(
            file=handle, mime_type='video/mp4',
            attributes=[types.DocumentAttributeVideo(duration=dur, w=w, h=h, supports_streaming=True),
                        types.DocumentAttributeFilename(file_name=fname)], thumb=t_file)
    elif media_type == 'photo':
        if isinstance(handle, types.InputFileBig):
            return types.InputMediaUploadedDocument(file=handle, mime_type='image/jpeg',
                                                    attributes=[types.DocumentAttributeFilename(file_name=fname)])
        return types.InputMediaUploadedPhoto(file=handle)
    else:
        mime_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        thumb_path = next(
            (t for t in [f"{os.path.splitext(path)[0]}_thumb.jpg", f"{path}_thumb.jpg"] if os.path.exists(t)), None)
        t_file = await client.upload_file(thumb_path) if thumb_path else None
        return types.InputMediaUploadedDocument(file=handle, mime_type=mime_type,
                                                attributes=[types.DocumentAttributeFilename(file_name=fname)],
                                                thumb=t_file)


async def send_album_with_fallback(client, target, handles, caps):
    """
    Attempts to send album to the target channel.
    If it fails due to HTML parse errors, it falls back to plain text captions.
    If it fails due to grouping errors, it attempts to split and send them individually.

    :param client: Telethon TelegramClient instance.
    :param target: Target channel/chat ID to send the media to.
    :param handles: List of prepared media handles/InputMedia objects.
    :param caps: List of captions associated with the respective media handles.
    :return: A list of the successfully sent Telethon Message objects.
    """
    if not handles: return []
    try:
        res = await client.send_file(target, handles, caption=caps, parse_mode='html')
        return res if isinstance(res, list) else [res]
    except ValueError as e:
        print(f"  ⚠️ HTML parse error in caption ({e}). Falling back to plain text...")
        plain_caps = [BeautifulSoup(c, 'html.parser').get_text() if c else "" for c in caps]
        res = await client.send_file(target, handles, caption=plain_caps, parse_mode=None)
        return res if isinstance(res, list) else [res]
    except Exception as e:
        err_str = str(e).lower()
        if any(k in err_str for k in ["invalid", "media", "empty", "part", "file", "multi"]):
            if len(handles) == 1:
                print(f"  ⚠️ Exception caught when uploading. Sending independently... ({type(e).__name__})")
                try:
                    res = await client.send_file(target, handles[0], caption=caps[0] if caps else "", parse_mode='html')
                except ValueError:
                    res = await client.send_file(target, handles[0], caption=BeautifulSoup(caps[0],
                                                                                           'html.parser').get_text() if caps else "",
                                                 parse_mode=None)
                return [res]
            else:
                print(
                    f"  ⚠️ Group upload failed. Splitting group...")
                mid = len(handles) // 2
                res1 = await send_album_with_fallback(client, target, handles[:mid], caps[:mid])
                res2 = await send_album_with_fallback(client, target, handles[mid:], caps[mid:])
                return res1 + res2
        elif any(k in err_str for k in ["parse", "entity", "entities", "tag"]):
            print(f"  ⚠️ Server HTML parse error in caption ({e}). Falling back to plain text...")
            plain_caps = [BeautifulSoup(c, 'html.parser').get_text() if c else "" for c in caps]
            res = await client.send_file(target, handles, caption=plain_caps, parse_mode=None)
            return res if isinstance(res, list) else [res]
        else:
            raise e


async def send_message(client, target, text):
    """
    Safely sends a pure text message to the target channel.
    It automatically chunks the message if it exceeds Telegram's 4096 character
    limit, and falls back to plain text if HTML parsing fails.

    :param client: Telethon TelegramClient instance.
    :param target: Target channel/chat ID.
    :param text: The text string to send.
    """
    if not text or not text.strip(): return
    try:
        if len(text) > 4096:
            for i in range(0, len(text), 4096): await send_message(client, target, text[i:i + 4096])
        else:
            await client.send_message(target, text, parse_mode='html')
    except ValueError as e:
        print(f"  ⚠️ Local HTML parse error in text message ({e}). Falling back to plain text...")
        await client.send_message(target, BeautifulSoup(text, 'html.parser').get_text(), parse_mode=None)
    except Exception as e:
        if any(k in str(e).lower() for k in ["parse", "entity", "entities", "tag"]):
            print(f"  ⚠️ Server HTML parse error in text message ({e}). Falling back to plain text...")
            await client.send_message(target, BeautifulSoup(text, 'html.parser').get_text(), parse_mode=None)
        else:
            print(f"❌ Failed to send text message: {e}")


async def process_and_upload(**kwargs):
    """
    The core asynchronous workflow.
    It sets up configuration, continuously monitors HTML export files, parses new
    albums, matches files locally, handles splitting/repacking of large files,
    uploads the files to the channel, and tracks progress to persist state.

    :param kwargs: Keyword arguments to configure the execution (from CLI).
    """
    set_args(**kwargs)
    init_logger()

    if not os.path.exists(HTML_FILE):
        print(f"⏳ Waiting for Telegram to generate {os.path.basename(HTML_FILE)}...")
        while not os.path.exists(HTML_FILE): await asyncio.sleep(2)
        print("✅ Found HTML file! Starting processing...\n")

    chat_name = get_chat_name(HTML_FILE)
    print(f"📌 Chat: '{chat_name}'")

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()
    registry = load_registry(chat_name)

    while True:
        all_html = get_all_html_files(HTML_FILE)
        albums = parse_messages(all_html)
        last_idx = load_progress(chat_name)

        if last_idx >= len(albums):
            leftover_count, old_size = check_folder_stats()
            if leftover_count > 0:
                next_num = len(all_html) + 1
                base_n = os.path.splitext(os.path.basename(HTML_FILE))[0]
                next_html_name = f"{base_n}{next_num}.html" if next_num > 1 else f"{base_n}.html"
                next_html_path = os.path.join(os.path.dirname(HTML_FILE), next_html_name)

                sys.stdout.write(f"\r⏳ Found {leftover_count} leftover files. Waiting for {next_html_name}...     ")
                sys.stdout.flush()

                elapsed = 0
                while not os.path.exists(next_html_path):
                    await asyncio.sleep(2)
                    elapsed += 2
                    if elapsed % 10 == 0:
                        sys.stdout.write(
                            f"\r⏳ Found {leftover_count} leftover files. Waiting for {next_html_name}... ({elapsed}s)     ")
                        sys.stdout.flush()
                print(f"\n✅ Found {next_html_name}! Parsing...")
                continue

            await asyncio.sleep(5)
            _, new_size = check_folder_stats()
            if new_size > old_size:
                sys.stdout.write(f"\r⏳ Folder size increasing. Telegram is actively downloading...     ")
                sys.stdout.flush()
                continue

            print("\n\n")
            print("=== All parsed messages are processed ===")
            user_input = await asyncio.to_thread(input,
                                                 "If Telegram is still exporting, press [ENTER] to rescan. Type 'exit' to finish: ")
            if user_input.strip().lower() in ['exit', 'quit', 'q']:
                print("✅ Export finished. Exiting script...")
                break
            continue

        for idx in range(last_idx + 1, len(albums) + 1):
            album_items, cur_cap_list = albums[idx - 1]
            full_caption = "\n\n".join(cur_cap_list)

            print(f"\n--- Group {idx}/{len(albums)} ---")
            resolved_items = []

            # Resolve media locally or from cloud
            for item in album_items:
                fname = item.get('filename')
                m_type = item['type']
                target_path = item.get('exact_path')
                d = item.get('folder')
                exp_size = item.get('expected_size', 0)

                item_resolved = False
                while not item_resolved:
                    print(f"\n🔍 Resolving {m_type}: {fname}")

                    if target_path and os.path.exists(target_path):
                        for _ in range(3):
                            try:
                                if os.path.getsize(target_path) > 0:
                                    with open(target_path, 'ab'): pass
                                    print(f"  ✅ Found exact match locally: {fname}")
                                    item['resolved_path'] = target_path
                                    resolved_items.append(item)
                                    item_resolved = True
                                    break
                            except OSError:
                                pass
                            await asyncio.sleep(1)
                    if item_resolved: break

                    reg_key = f"{fname}_{exp_size}"
                    cloud_id = registry.get(reg_key) or (registry.get(fname) if exp_size == 0 else None)

                    if cloud_id:
                        print(f"  ☁️ Found in Cloud Registry. Reusing ID: {cloud_id}")
                        item['cloud_msg_id'] = cloud_id
                        item['resolved_path'] = None
                        resolved_items.append(item)
                        item_resolved = True
                        break

                    print(f"  ⚠️ Exact name missing locally/Cloud. Falling back...")
                    exp_dur = item.get('expected_duration', 0)
                    available_files = []
                    if d and os.path.exists(d):
                        for f in os.listdir(d):
                            if not os.path.isfile(
                                    os.path.join(d, f)) or "_thumb" in f.lower() or ".deleted" in f.lower(): continue
                            try:
                                with open(os.path.join(d, f), 'ab'):
                                    pass
                                available_files.append(f)
                            except OSError:
                                pass

                    best_match = None
                    for af in available_files:
                        af_path = os.path.join(d, af)
                        sz = os.path.getsize(af_path)
                        size_match = (exp_size == 0 and sz > 0) or (
                                exp_size > 0 and abs(sz - exp_size) / exp_size < 0.1)
                        if m_type == 'video':
                            dur = await asyncio.to_thread(get_video_duration, af_path)
                            dur_match = (exp_dur == 0 and dur > 0) or (exp_dur > 0 and abs(dur - exp_dur) <= 5)
                            if size_match and dur_match:
                                best_match = af_path
                                break
                        else:
                            if size_match:
                                best_match = af_path
                                break

                    if best_match:
                        print(f"  ✅ Found match by metadata: {os.path.basename(best_match)}")
                        item['resolved_path'] = best_match
                        resolved_items.append(item)
                        item_resolved = True
                        break

                    print("\n=== Failed to resolve media ===")
                    action = await asyncio.to_thread(input,
                                                     "Action: [W]ait for Telegram, [S]kip media, [A]bort script: ")
                    if action.strip().lower() == 'w':
                        await asyncio.sleep(5)
                    elif action.strip().lower() == 's':
                        item_resolved = True
                    elif action.strip().lower() == 'a':
                        sys.exit(1)

            # Process resolved files
            flat_items = []
            files_to_delete = []

            async def process_large_files(parts_list, parent_item, is_archive=False):
                """
                Iterates and uploads split file chunks (either video parts or repacked archives)
                individually to the channel and records them immediately into the registry/state.

                :param parts_list: List of local paths corresponding to chunk files.
                :param parent_item: The original parent media item dict.
                :param is_archive: Boolean representing if it's a repacked archive.
                """
                for p in parts_list:
                    p_fname = os.path.basename(p)
                    p_reg_key = f"{p_fname}_0"
                    p_cloud_id = registry.get(p_reg_key) or registry.get(p_fname)

                    if p_cloud_id:
                        print(f"  ☁️ Chunk '{p_fname}' already in Cloud Registry. Skipping.")
                        continue

                    print(f"  -> Uploading and committing '{p_fname}' individually...")
                    files_to_delete.append(p)
                    h = await build_input_media(client, p, parent_item['type'])

                    try:
                        sent_msg = await client.send_file(TARGET_CHANNEL, h, caption=p_fname)
                        if sent_msg:
                            # Update Cloud Registry immediately
                            new_reg = {p_reg_key: sent_msg.id}
                            registry.update(new_reg)
                            save_registry(chat_name, new_reg)

                            # Update Repack State JSON
                            if is_archive:
                                state = load_repack_state()
                                arch_key = os.path.basename(parent_item['resolved_path'])
                                if arch_key in state:
                                    if "uploaded_slices" not in state[arch_key]:
                                        state[arch_key]["uploaded_slices"] = []
                                    if p_fname not in state[arch_key]["uploaded_slices"]:
                                        state[arch_key]["uploaded_slices"].append(p_fname)
                                        save_repack_state(state)
                    except Exception as e:
                        print(f"\n❌ Exception caught when sending chunk {p_fname}: {e}")
                        sys.exit(1)

            for item in resolved_items:
                if item.get('cloud_msg_id'):
                    msg = await client.get_messages(TARGET_CHANNEL, ids=item['cloud_msg_id'])
                    if msg and msg.media: flat_items.append({'item': item, 'handle': msg.media})
                else:
                    path = item['resolved_path']
                    file_size = os.path.getsize(path)

                    if item['type'] == 'video' and file_size > MAX_FILE_SIZE:
                        parts = await asyncio.to_thread(split_video, path)
                        files_to_delete.append(path)
                        await process_large_files(parts, item, is_archive=False)

                    elif item['type'] != 'video' and file_size > MAX_FILE_SIZE:
                        ext = os.path.splitext(path)[1].lower()
                        if ext in ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz']:
                            print(f"\n📦 Archive '{item['filename']}' exceeds API Limit. Repacking...")
                            parts = await repack_large_archive(path, os.path.dirname(path))
                            if not parts:
                                action = await asyncio.to_thread(input, "Failed to repack. (y to skip, else force): ")
                                if action.strip().lower() == 'y': continue
                                files_to_delete.append(path)
                                flat_items.append(
                                    {'item': item, 'handle': await build_input_media(client, path, item['type'])})
                            else:
                                files_to_delete.append(path)
                                await process_large_files(parts, item, is_archive=True)
                        else:
                            print(
                                f"\n🚨 Non-video file '{item['filename']}' is {file_size / (1024 ** 3):.2f}GB.")
                            action = await asyncio.to_thread(input, "Skip item? (y to skip, else force): ")
                            if action.strip().lower() == 'y': continue
                            files_to_delete.append(path)
                            flat_items.append(
                                {'item': item, 'handle': await build_input_media(client, path, item['type'])})
                    else:
                        files_to_delete.append(path)
                        flat_items.append({'item': item, 'handle': await build_input_media(client, path, item['type'])})

            if not flat_items and full_caption:
                print("📝 Sending standalone text message...")
                for text_msg in cur_cap_list: await send_message(client, TARGET_CHANNEL, text_msg)

            if not flat_items:
                save_progress(chat_name, idx)
                continue

            # Upload batched albums to channel for standard file < MAX_FILE_SIZE
            if len(full_caption) > MAX_CAPTION_LENGTH:
                media_caption, leftover_text_list = "", cur_cap_list
            else:
                media_caption, leftover_text_list = full_caption, []

            sub_arrays = [flat_items[i:i + 10] for i in range(0, len(flat_items), 10)]
            sent_messages = []
            resolved_paths, cloud_reuses = [], []

            for sub_idx, sub_array in enumerate(sub_arrays):
                handles = [x['handle'] for x in sub_array]
                caps = [""] * len(handles)
                if sub_idx == 0 and handles: caps[0] = media_caption

                if handles:
                    try:
                        print(f"  🎬 Finalizing post to channel (Batch {sub_idx + 1}/{len(sub_arrays)})...")
                        sent_msgs = await send_album_with_fallback(client, TARGET_CHANNEL, handles, caps)
                        sent_messages.extend(sent_msgs)

                        batch_registry_entries = {}
                        for x, sent_msg in zip(sub_array, sent_msgs):
                            it = x['item']
                            fname, exp_sz = it.get('filename'), it.get('expected_size', 0)
                            reg_key = f"{fname}_{exp_sz}"

                            if fname and not it.get('cloud_msg_id') and sent_msg:
                                batch_registry_entries[reg_key] = sent_msg.id

                            if it.get('cloud_msg_id'):
                                cloud_reuses.append(fname)
                            elif it.get('resolved_path'):
                                resolved_paths.append(it['resolved_path'])

                        if batch_registry_entries:
                            save_registry(chat_name, batch_registry_entries)
                            registry.update(batch_registry_entries)

                    except Exception as album_err:
                        print(f"\n❌ Critical Final send failed: {album_err}")
                        sys.exit(1)

            if leftover_text_list:
                print("  📝 Sending leftover text...")
                for text_msg in leftover_text_list: await send_message(client, TARGET_CHANNEL, text_msg)

            print("\n" + "=" * 50)
            print("📝 Text / Caption Uploaded:")
            print(full_caption if full_caption.strip() else "(No text)")
            if resolved_paths:
                print("\n📁 Local Files Uploaded:")
                for p in resolved_paths: print(f"  - {p}")
            if cloud_reuses:
                print("\n☁️ Duplicates Reused from Telegram Cloud:")
                for c in set(cloud_reuses): print(f"  - {c}")
            print("=" * 50)

            save_progress(chat_name, idx)
            if DELETE_FILE:
                print("✅ Confirmed! Progress saved. Deleting local files...\n")
            else:
                print("✅ Confirmed! Progress saved. Renaming local files to '.deleted'...\n")

            deletion_failed = False
            for f in set(files_to_delete):
                for attempt in range(3):
                    try:
                        if DELETE_FILE:
                            if os.path.exists(f):
                                os.remove(f)
                            for t in [f"{os.path.splitext(f)[0]}_thumb.jpg", f"{f}_thumb.jpg"]:
                                if os.path.exists(t):
                                    os.remove(t)
                        else:
                            if os.path.exists(f) and ".deleted" not in f.lower():
                                new_del = f + ".deleted"
                                counter = 1
                                while os.path.exists(new_del):
                                    new_del = f"{f}.deleted.{counter}"
                                    counter += 1
                                os.rename(f, new_del)

                            for t in [f"{os.path.splitext(f)[0]}_thumb.jpg", f"{f}_thumb.jpg"]:
                                if os.path.exists(t) and ".deleted" not in t.lower():
                                    new_t_del = t + ".deleted"
                                    counter = 1
                                    while os.path.exists(new_t_del):
                                        new_t_del = f"{t}.deleted.{counter}"
                                        counter += 1
                                    os.rename(t, new_t_del)
                        break
                    except OSError as e:
                        if attempt == 2:
                            print(f"\n  ⚠️ Deletion marker failed {os.path.basename(f)}: {e}")
                            deletion_failed = True
                        await asyncio.sleep(0.5)

            if deletion_failed:
                print(f"❌ Aborted due to failed deletions.")
                sys.exit(1)

            await asyncio.sleep(2)


def main():
    parser = argparse.ArgumentParser(description="Telegram upload exporter")
    parser.add_argument("--api-id", type=int, help="Telegram API ID")
    parser.add_argument("--api-hash", type=str, help="Telegram API Hash")
    parser.add_argument("--session-name", type=str, default="tg_upload", help="Session Name")
    parser.add_argument("--target-channel", type=int, help="Target Channel ID (e.g. -100XXXXXX)")
    parser.add_argument("--html-file", type=str, help="HTML File path generated by Telegram")
    parser.add_argument("--seven-zip-path", type=str, default=r"C:\Program Files\7-Zip\7z.exe", help="7-Zip executable path")
    parser.add_argument("--archive-password", type=str, help="Archive decryption password")
    parser.add_argument("--progress-file", type=str, help="Progress JSON file name")
    parser.add_argument("--registry-file", type=str, help="Registry JSON file name")
    parser.add_argument("--repack-state-file", type=str, help="Repack state JSON file name")
    parser.add_argument("--log-file", type=str, help="Log file path")
    parser.add_argument("--max-caption-length", type=int, help="Limit max characters per post caption.")
    parser.add_argument("--delete-file", action="store_true",
                        help="Delete uploaded local files instead of renaming them with '.deleted'.")
    parser.add_argument("--premium", action="store_true",
                        help="Enable Telegram Premium mode (Extends limits to 4GB files & 3900MB archives).")

    args = parser.parse_args()

    # Filter out None values to prioritize .env fallbacks correctly
    kwargs = {k: v for k, v in vars(args).items() if v is not None}

    if "premium" in kwargs:
        kwargs["is_premium"] = kwargs.pop("premium")

    try:
        asyncio.run(process_and_upload(**kwargs))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"\nExecution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()