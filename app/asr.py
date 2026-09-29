"""Speech recognition behind one interface.

Two backends produce the same `Word` list so nothing downstream knows which ran:

  mlx            — mlx-whisper on Metal. Default on Apple Silicon.
  faster_whisper — CTranslate2 on CPU. Portable, but has no Metal support, so on
                   an Apple Silicon Mac it leaves the GPU idle.

Models are loaded lazily and cached, because the first load pulls ~1.5GB.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger(__name__)

_model_lock = threading.Lock()
_faster_model = None


@dataclass
class Word:
    start: float
    end: float
    text: str
    channel: str = "mixed"

    def as_dict(self) -> dict:
        return asdict(self)


class ASRError(RuntimeError):
    pass


def transcribe(
    wav: Path,
    *,
    channel: str = "mixed",
    offset: float = 0.0,
    initial_prompt: str | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> list[Word]:
    """Transcribe a 16 kHz mono wav into word-level timings.

    `offset` shifts every timestamp, which is what lets live segments report
    times on the whole-session timeline instead of restarting at zero.
    `on_progress` receives the fraction of audio covered so far (0-1); only
    faster-whisper reports it incrementally, mlx reports once at the end.
    """
    backend = config.ASR_BACKEND
    if backend == "mlx":
        words = _transcribe_mlx(wav, initial_prompt)
        if on_progress:
            on_progress(1.0)
    elif backend == "faster_whisper":
        words = _transcribe_faster(wav, initial_prompt, on_progress)
    else:
        raise ASRError(
            f"Unknown ASR_BACKEND {backend!r}. Use 'mlx' or 'faster_whisper'."
        )

    return [
        Word(start=w[0] + offset, end=w[1] + offset, text=w[2], channel=channel)
        for w in words
    ]


def _transcribe_mlx(wav: Path, initial_prompt: str | None) -> list[tuple]:
    try:
        import mlx_whisper
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ASRError(
            "mlx-whisper is not installed. Install it, or set "
            "ASR_BACKEND=faster_whisper in .env."
        ) from exc

    result = mlx_whisper.transcribe(
        str(wav),
        path_or_hf_repo=config.MLX_WHISPER_MODEL,
        word_timestamps=True,
        initial_prompt=initial_prompt,
    )
    out: list[tuple] = []
    for segment in result.get("segments", []):
        for word in segment.get("words") or []:
            text = (word.get("word") or "").strip()
            if text:
                out.append((float(word["start"]), float(word["end"]), text))
    return out


def _transcribe_faster(
    wav: Path,
    initial_prompt: str | None,
    on_progress: Callable[[float], None] | None,
) -> list[tuple]:
    global _faster_model
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ASRError("faster-whisper is not installed.") from exc

    with _model_lock:
        if _faster_model is None:
            log.info("Loading faster-whisper %s (CPU, %d threads)",
                     config.FASTER_WHISPER_MODEL, config.CPU_THREADS)
            _faster_model = WhisperModel(
                config.FASTER_WHISPER_MODEL, device="cpu", compute_type="int8",
                cpu_threads=config.CPU_THREADS,
            )

    # `segments` is a lazy generator: decoding happens as it is consumed, so
    # each yielded segment's end time is real progress through the file.
    segments, info = _faster_model.transcribe(
        str(wav), word_timestamps=True, initial_prompt=initial_prompt
    )
    duration = float(info.duration or 0.0)
    out: list[tuple] = []
    for segment in segments:
        if on_progress and duration > 0:
            on_progress(min(1.0, float(segment.end) / duration))
        for word in segment.words or []:
            text = (word.word or "").strip()
            if text:
                out.append((float(word.start), float(word.end), text))
    return out
