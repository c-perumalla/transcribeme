"""Enumerate audio inputs: avfoundation on macOS, DirectShow on Windows.

ffmpeg prints the device list to stderr and exits non-zero — that is normal, not
a failure. On macOS, avfoundation *indexes shift* as devices appear and
disappear, so callers resolve by name and we re-enumerate on every start.
DirectShow addresses devices by name directly, so there is nothing to drift.
"""

from __future__ import annotations

import re
import subprocess

from . import config

_AUDIO_HEADER = "AVFoundation audio devices:"
_ENTRY = re.compile(r"\[(\d+)\]\s+(.+?)\s*$")

# ffmpeg >= 5 tags each dshow device inline:  "Name" (audio)
_DSHOW_INLINE = re.compile(r'"(.+)"\s+\((audio|video|audio, video|none)\)\s*$')
# Older builds group devices under section headers instead.
_DSHOW_QUOTED = re.compile(r'^\s*"(.+)"\s*$')


class DeviceError(RuntimeError):
    pass


def _ffmpeg_stderr(args: list[str]) -> str:
    proc = subprocess.run(
        [config.FFMPEG, "-hide_banner", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stderr


def list_audio_devices() -> list[dict]:
    """Return [{index, name, is_aggregate}] for every audio input."""
    if config.IS_WINDOWS:
        devices = parse_dshow(_ffmpeg_stderr(
            ["-list_devices", "true", "-f", "dshow", "-i", "dummy"]
        ))
        if not devices:
            raise DeviceError(
                "ffmpeg reported no DirectShow audio devices. Check that a "
                "microphone is connected and enabled under Settings > System > "
                "Sound, and that 'Let desktop apps access your microphone' is on "
                "under Settings > Privacy & security > Microphone."
            )
        return devices

    devices = parse_avfoundation(_ffmpeg_stderr(
        ["-f", "avfoundation", "-list_devices", "true", "-i", ""]
    ))
    if not devices:
        raise DeviceError(
            "ffmpeg reported no avfoundation audio devices. If this is the first "
            "run, macOS may be waiting on a microphone permission prompt for the "
            "terminal app running the server — grant it in System Settings > "
            "Privacy & Security > Microphone, then restart."
        )
    return devices


def parse_avfoundation(stderr: str) -> list[dict]:
    devices: list[dict] = []
    in_audio = False
    for line in stderr.splitlines():
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
    return devices


def parse_dshow(stderr: str) -> list[dict]:
    names: list[str] = []
    section: str | None = None  # only used by the older header format

    for line in stderr.splitlines():
        payload = line.split("] ", 1)[-1] if line.lstrip().startswith("[") else line
        if "Alternative name" in payload:
            continue
        if "DirectShow audio devices" in payload:
            section = "audio"
            continue
        if "DirectShow video devices" in payload:
            section = "video"
            continue

        inline = _DSHOW_INLINE.search(payload)
        if inline:
            if "audio" in inline.group(2):
                names.append(inline.group(1))
            continue

        quoted = _DSHOW_QUOTED.match(payload)
        if quoted and section == "audio":
            names.append(quoted.group(1))

    unique = list(dict.fromkeys(names))
    return [
        {"index": i, "name": name, "is_aggregate": False}
        for i, name in enumerate(unique)
    ]


def resolve(name: str) -> int:
    """Map a device name to its *current* index."""
    devices = list_audio_devices()
    for dev in devices:
        if dev["name"] == name:
            return dev["index"]
    available = ", ".join(d["name"] for d in devices)
    raise DeviceError(f"Audio device {name!r} is not available. Found: {available}")


def input_args(name: str) -> list[str]:
    """The ffmpeg input flags that open this device on the current platform."""
    index = resolve(name)  # validates the name on both platforms
    if config.IS_WINDOWS:
        # Passed as one argv element, so names with spaces/parens need no quoting.
        return ["-f", "dshow", "-i", f"audio={name}"]
    return ["-f", "avfoundation", "-i", f":{index}"]
