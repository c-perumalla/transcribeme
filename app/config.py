"""Environment-backed settings. Read once at import."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


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
ASR_BACKEND = os.getenv("ASR_BACKEND", "mlx").strip().lower()
MLX_WHISPER_MODEL = os.getenv("MLX_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
FASTER_WHISPER_MODEL = os.getenv("FASTER_WHISPER_MODEL", "large-v3-turbo")

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
DRIVE_DIR = _path("DRIVE_DIR", "~/Google Drive/My Drive/TranscribeMe")

# Server
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

FFMPEG = os.getenv("FFMPEG", "ffmpeg")
FFPROBE = os.getenv("FFPROBE", "ffprobe")

# Gap between words that forces a new utterance, in seconds.
UTTERANCE_GAP_S = float(os.getenv("UTTERANCE_GAP_S", "1.5"))


def ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
