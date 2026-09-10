"""Enumerate macOS avfoundation audio inputs.

ffmpeg prints the device list to stderr and exits non-zero — that is normal, not
a failure. Device *indexes shift* as devices appear and disappear, so callers
resolve by name and we re-enumerate on every start.
"""

from __future__ import annotations

import re
import subprocess

from . import config

_AUDIO_HEADER = "AVFoundation audio devices:"
_ENTRY = re.compile(r"\[(\d+)\]\s+(.+?)\s*$")


class DeviceError(RuntimeError):
    pass


def list_audio_devices() -> list[dict]:
    """Return [{index, name, is_aggregate}] for every avfoundation audio input."""
    proc = subprocess.run(
        [config.FFMPEG, "-hide_banner", "-f", "avfoundation",
         "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    )
    lines = proc.stderr.splitlines()

    devices: list[dict] = []
    in_audio = False
    for line in lines:
        if _AUDIO_HEADER in line:
            in_audio = True
            continue
        if not in_audio:
            continue
        # The audio block is the last one; a line without a bracketed index ends it.
        payload = line.split("] ", 1)[-1] if "] " in line else line
        match = _ENTRY.match(payload.strip())
        if not match:
            if devices:
                break
            continue
        name = match.group(2)
        devices.append({
            "index": int(match.group(1)),
            "name": name,
            "is_aggregate": "aggregate" in name.lower(),
        })

    if not devices:
        raise DeviceError(
            "ffmpeg reported no avfoundation audio devices. If this is the first "
            "run, macOS may be waiting on a microphone permission prompt for the "
            "terminal app running the server — grant it in System Settings > "
            "Privacy & Security > Microphone, then restart."
        )
    return devices


def resolve(name: str) -> int:
    """Map a device name to its *current* avfoundation index."""
    devices = list_audio_devices()
    for dev in devices:
        if dev["name"] == name:
            return dev["index"]
    available = ", ".join(d["name"] for d in devices)
    raise DeviceError(f"Audio device {name!r} is not available. Found: {available}")
