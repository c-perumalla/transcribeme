"""Environment-backed settings. Read once at import."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_APPLE_SILICON = IS_MAC and platform.machine() == "arm64"

# Without Developer Mode, Windows cannot create the symlinks the HF cache
# prefers; it falls back to copies, which is fine, but warns on every load.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: str) -> Path:
    return Path(os.path.expanduser(os.getenv(name) or default))


# Claude
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or None
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")

# Diarization
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_TOKEN") or None
DIARIZATION_MODEL = os.getenv(
    "DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1"
)

# ASR
# 'auto' picks mlx where Metal exists and faster-whisper everywhere else.
ASR_BACKEND = os.getenv("ASR_BACKEND", "auto").strip().lower()
if ASR_BACKEND == "auto":
    ASR_BACKEND = "mlx" if IS_APPLE_SILICON else "faster_whisper"
MLX_WHISPER_MODEL = os.getenv("MLX_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
FASTER_WHISPER_MODEL = os.getenv("FASTER_WHISPER_MODEL", "large-v3-turbo")
# CTranslate2 defaults to 4 threads; on a CPU-only machine every core counts.
CPU_THREADS = int(os.getenv("CPU_THREADS") or 0) or (os.cpu_count() or 4)

# Capture
SEGMENT_SECONDS = int(os.getenv("SEGMENT_SECONDS", "15"))
MY_NAME = os.getenv("MY_NAME", "Me")
DIARIZE_MIC_CHANNEL = _bool("DIARIZE_MIC_CHANNEL", False)

# Paths
DATA_DIR = _path("DATA_DIR", str(ROOT / "data"))
SESSIONS_DIR = DATA_DIR / "sessions"
DB_PATH = DATA_DIR / "transcribeme.db"
SKILLS_DIR = _path("SKILLS_DIR", str(ROOT / "skills"))
STATIC_DIR = ROOT / "static"
# Drive for Desktop mounts as G: on Windows by default.
DRIVE_DIR = _path(
    "DRIVE_DIR",
    "G:/My Drive/TranscribeMe" if IS_WINDOWS else "~/Google Drive/My Drive/TranscribeMe",
)

# Server
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# setup.ps1 unpacks a pinned ffmpeg here, so Windows needs nothing on PATH.
BUNDLED_FFMPEG_BIN = ROOT / ".tools" / "ffmpeg" / "bin"


def _tool(name: str) -> str:
    """Resolve ffmpeg/ffprobe: env var, then the bundled build, then PATH."""
    explicit = os.getenv(name.upper())
    if explicit:
        return explicit
    bundled = BUNDLED_FFMPEG_BIN / (name + (".exe" if IS_WINDOWS else ""))
    if bundled.is_file():
        return str(bundled)
    return shutil.which(name) or name


def _expose_bundled_ffmpeg() -> None:
    """Make the bundled ffmpeg DLLs loadable by torchcodec (a pyannote dep).

    Python 3.8+ on Windows no longer searches PATH for extension-module DLLs,
    so the directory has to be registered explicitly as well.
    """
    if not BUNDLED_FFMPEG_BIN.is_dir():
        return
    os.environ["PATH"] = str(BUNDLED_FFMPEG_BIN) + os.pathsep + os.environ.get("PATH", "")
    if IS_WINDOWS:
        os.add_dll_directory(str(BUNDLED_FFMPEG_BIN))


_expose_bundled_ffmpeg()
FFMPEG = _tool("ffmpeg")
FFPROBE = _tool("ffprobe")

# Gap between words that forces a new utterance, in seconds.
UTTERANCE_GAP_S = float(os.getenv("UTTERANCE_GAP_S", "1.5"))


def ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
