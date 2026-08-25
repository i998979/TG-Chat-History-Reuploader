# Telegram Export Uploader

An asynchronous Python tool designed to parse Telegram Desktop HTML exports
and automatically upload media files, albums, and formatted text messages back to a target Telegram channel.

It handles large file splitting, archive repacking, real-time export progress tracking,
and duplicate cloud re-use to ensure smooth migration or backup operations.

Useful to export chat history with bots banned by Telegram,
where all chat records have been replaced by Telegram's terms violation notice.

---

## Features

* **HTML Parsing & Formatting**: Parses exported HTML pages, retaining inline formatting (`<b>`, `<i>`, `<a>`, `<code>`, `<blockquote>`) and grouping media into albums.
* **Smart File Processing & Splitting**:
  * **Videos**: Large video files (> 2GB / 4GB) are automatically split using `FFmpeg`.
  * **Archives**: Zip/Rar/7z archives exceeding size limits are extracted and repacked into sliced volumes using `7-Zip`.
* **Telegram Premium Support**: Dynamic file limit handling (2 GB for free accounts, 4 GB for Premium accounts).
* **Parallel High-Speed Uploads**: Utilizes custom chunked parallel uploads with automatic flood-wait handling.
* **Resumable State Management**: Maintains progress, cloud media registry, and repack state to safely resume uninterrupted after a crash or network error.
* **Active Export Monitoring**: Continuously waits for ongoing Telegram Desktop exports to complete new HTML files or download local media files.
* **Cleanup Strategy**: Automatically marks uploaded files by adding a `.deleted` extension or permanently deleting them upon completion.

---

## Prerequisites

### 1. Python Environment
Python 3.8 or higher.

### 2. External Dependencies
* **FFmpeg / FFprobe**: Must be installed and accessible in your system's `PATH`.
* **7-Zip**: Required for repacking large archives. Default path target is `C:\Program Files\7-Zip\7z.exe`.

---

## Installation

1. **Clone the repository or download the script.**

2. **Install required Python packages:**
   ```bash
   pip install -r requirements.txt
   ```

---

## Configuration

You can configure the tool using a `.env` file in the project root folder or by passing parameters directly via the Command Line Interface (CLI).

### Setting up `.env`

Create a `.env` file in the same directory:

```env
API_ID=1234567
API_HASH=your_telegram_api_hash
TARGET_CHANNEL=-1001234567890
HTML_FILE=C:/Downloads/Telegram Export/messages.html
ARCHIVE_PASSWORD=optional_archive_password
```

> **How to get Telegram API Credentials:**
> 1. Log in to [my.telegram.org](https://my.telegram.org).
> 2. Go to **API development tools** and create a new application to obtain your `API_ID` and `API_HASH`.

---

## Usage

1. Export chat history you want to re-upload. If you find difficulties with Official Telegram App, try [Ayugram](https://github.com/AyuGram/AyuGramDesktop)

2. Run the script from the command line using your session settings and configuration parameters:

```bash
python uploader.py --session-name my_session --delete-file --premium
```

### Command-Line Arguments

| Parameter              | Type   | Description                                                                               |
|:-----------------------|:-------|:------------------------------------------------------------------------------------------|
| `--api-id`             | `int`  | Telegram API ID (overrides `.env`).                                                       |
| `--api-hash`           | `str`  | Telegram API Hash (overrides `.env`).                                                     |
| `--session-name`       | `str`  | Telethon session file name (default: `tg_upload`).                                        |
| `--target-channel`     | `int`  | Target Telegram channel/chat ID (e.g., `-1001234567890`).                                 |
| `--html-file`          | `str`  | Path to the entry `messages.html` file of the export folder.                              |
| `--seven-zip-path`     | `str`  | Path to the 7-Zip executable (default: `C:\Program Files\7-Zip\7z.exe`).                  |
| `--archive-password`   | `str`  | Password for encrypted archives if repacking is required.                                 |
| `--delete-file`        | `flag` | Delete local media files after successful upload instead of renaming to `.deleted`.       |
| `--premium`            | `flag` | Enables Telegram Premium limits (increases file size limit to 4GB).                       |
| `--max-caption-length` | `int`  | Maximum allowed character length for message captions (default: `1000`).                  |
| `--progress-file`      | `str`  | File name for tracking progress index (default: `tg_upload_progress.json`).               |
| `--registry-file`      | `str`  | File name for tracking uploaded cloud message IDs (default: `tg_upload_registry.json`).   |
| `--repack-state-file`  | `str`  | File name for tracking archive extraction/repacking state (default: `repack_state.json`). |
| `--log-file`           | `str`  | Path to log terminal output (default: `upload_log.txt`).                                  |

---

## Generated Files & State Tracking

During execution, the script generates several JSON tracking files to support process recovery and avoid double-posting:

* **`tg_upload_progress.json`**: Saves the current processed message group index per chat.
* **`tg_upload_registry.json`**: Stores mappings between local filenames/sizes and Telegram Cloud Message IDs to eliminate duplicate uploads.
* **`repack_state.json`**: Records extraction and split progress for oversized archive files.
* **`upload_log.txt`**: Detailed output log of terminal activity.