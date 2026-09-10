"""ffmpeg helpers: probing, normalizing, and splitting the capture channels."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config


class AudioError(RuntimeError):
    pass


def _run(args: list[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-4:]
        raise AudioError(" / ".join(tail) or f"{args[0]} failed")
    return proc.stdout


def probe(path: Path) -> dict:
    """Return {duration_s, channels, sample_rate} for an audio file."""
    out = _run([
        config.FFPROBE, "-v", "error", "-show_entries",
        "stream=channels,sample_rate:format=duration",
        "-select_streams", "a:0", "-of", "json", str(path),
    ])
    data = json.loads(out)
    streams = data.get("streams") or []
    if not streams:
        raise AudioError(f"{path.name} contains no audio stream")
    stream = streams[0]
    return {
        "duration_s": float(data.get("format", {}).get("duration") or 0.0),
        "channels": int(stream.get("channels") or 1),
        "sample_rate": int(stream.get("sample_rate") or 0),
    }


def normalize(src: Path, dst: Path) -> Path:
    """Downmix to 16 kHz mono PCM — what both Whisper and pyannote want."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst),
    ])
    return dst


def split_channels(src: Path, out_dir: Path) -> dict[str, Path]:
    """Split an aggregate-device capture into 'me' and 'them' mono tracks.

    The Aggregate Device puts the mic on channel 0 and BlackHole on channels 1+.
    That physical separation is what makes 'me' vs 'them' exact rather than
    inferred — the mic track needs no diarization at all.

    A single-channel capture has no far end to separate, so it returns just
    {'mixed': ...} and the caller falls back to the plain diarized path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    channels = probe(src)["channels"]

    if channels < 2:
        return {"mixed": normalize(src, out_dir / "mixed.wav")}

    me = out_dir / "me.wav"
    them = out_dir / "them.wav"

    # Everything past channel 0 is the far end; average it down to mono.
    far = list(range(1, channels))
    weight = 1.0 / len(far)
    them_expr = "+".join(f"{weight}*c{i}" for i in far)

    _run([
        config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-filter_complex",
        f"[0:a]pan=mono|c0=c0,aresample=16000[me];"
        f"[0:a]pan=mono|c0={them_expr},aresample=16000[them]",
        "-map", "[me]", "-c:a", "pcm_s16le", str(me),
        "-map", "[them]", "-c:a", "pcm_s16le", str(them),
    ])
    return {"me": me, "them": them}
