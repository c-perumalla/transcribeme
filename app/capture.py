"""Live audio capture through ffmpeg's avfoundation input.

One ffmpeg process, one input, two outputs:

  full.wav      the master used for the accurate post-stop pass
  seg/%05d.wav  short segments the live transcriber consumes

Two outputs from one process avoids tailing a growing file, and guarantees the
live text and the master come from exactly the same audio. Channel count is
preserved (no `-ac`) because the aggregate-device split depends on it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import config, devices

log = logging.getLogger(__name__)


class CaptureError(RuntimeError):
    pass


@dataclass
class Recorder:
    session_id: str
    device_name: str
    directory: Path
    process: asyncio.subprocess.Process
    stderr_tail: list[str] = field(default_factory=list)

    @property
    def full_wav(self) -> Path:
        return self.directory / "full.wav"

    @property
    def segment_dir(self) -> Path:
        return self.directory / "seg"


_recorders: dict[str, Recorder] = {}


def get(session_id: str) -> Recorder | None:
    return _recorders.get(session_id)


async def start(session_id: str, device_name: str) -> Recorder:
    if session_id in _recorders:
        raise CaptureError("This session is already recording.")

    index = devices.resolve(device_name)  # resolve by name; indexes shift

    directory = config.SESSIONS_DIR / session_id
    segment_dir = directory / "seg"
    segment_dir.mkdir(parents=True, exist_ok=True)

    args = [
        config.FFMPEG, "-hide_banner", "-loglevel", "warning", "-y",
        "-f", "avfoundation", "-i", f":{index}",
        # Without per-packet flushing the WAV sits in the muxer buffer, so a
        # healthy recording can look like a dead one for several seconds.
        "-flush_packets", "1",
        "-map", "0", "-c:a", "pcm_s16le", "-ar", "16000",
        str(directory / "full.wav"),
        "-map", "0", "-c:a", "pcm_s16le", "-ar", "16000",
        "-f", "segment", "-segment_time", str(config.SEGMENT_SECONDS),
        str(segment_dir / "%05d.wav"),
    ]
    # stdin stays open (no -nostdin) so stop() can send 'q'.

    log.info("Starting capture for %s on %r (index %d)", session_id, device_name, index)
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    recorder = Recorder(session_id, device_name, directory, process)
    _recorders[session_id] = recorder
    asyncio.create_task(_drain_stderr(recorder))

    try:
        await _confirm_audio_flowing(recorder)
    except CaptureError:
        _recorders.pop(session_id, None)
        await _kill(process)
        raise

    return recorder


# A PCM WAV header is 44 bytes; anything beyond that is real audio.
WAV_HEADER_BYTES = 44
STARTUP_GRACE_S = 8.0


async def _confirm_audio_flowing(recorder: Recorder) -> None:
    """Fail fast if the device yields no samples.

    Without this, a silent device looks like a healthy recording: ffmpeg stays
    alive and writes nothing, so the user records a whole meeting into silence
    and only finds out at stop. The common macOS cause is the microphone TCC
    grant — avfoundation opens the device and then never delivers frames — but
    ffmpeg's own stderr is the authority, so it is included in the message.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STARTUP_GRACE_S
    process = recorder.process

    while loop.time() < deadline:
        if process.returncode is not None:
            raise CaptureError(
                f"Could not open {recorder.device_name!r}. ffmpeg said: "
                + (_ffmpeg_detail(recorder) or "it exited immediately with no message.")
            )

        if _bytes_captured(recorder) > 0:
            return

        await asyncio.sleep(0.25)

    raise CaptureError(
        f"{recorder.device_name!r} opened but delivered no audio in "
        f"{STARTUP_GRACE_S:.0f}s. ffmpeg said: "
        + (_ffmpeg_detail(recorder) or "(nothing)")
        + " — On macOS the usual cause is the microphone permission, which "
        "belongs to the app that LAUNCHED this server (your IDE, if you started "
        "it from an integrated terminal — not Terminal.app). Check System "
        "Settings > Privacy & Security > Microphone, then fully quit and "
        "reopen that app; a running app does not pick up a new grant. Run "
        "./diagnose_mic.sh to test ffmpeg directly."
    )


def _bytes_captured(recorder: Recorder) -> int:
    """Audio written so far, across both outputs.

    Either output producing data proves the device is live, and the segment
    muxer often writes before the master does.
    """
    total = 0
    if recorder.full_wav.exists():
        total += max(0, recorder.full_wav.stat().st_size - WAV_HEADER_BYTES)
    if recorder.segment_dir.is_dir():
        for segment in recorder.segment_dir.glob("*.wav"):
            total += max(0, segment.stat().st_size - WAV_HEADER_BYTES)
    return total


def _ffmpeg_detail(recorder: Recorder) -> str:
    """The stderr lines worth showing, minus ffmpeg's routine chatter."""
    noise = ("Output #", "Stream mapping", "Press [q]", "encoder", "Metadata:",
             "configuration:", "built with", "Input #", "Duration:")
    lines = [
        line for line in recorder.stderr_tail
        if line and not any(line.lstrip().startswith(n) for n in noise)
    ]
    return " / ".join(lines[-3:])


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.kill()          # nothing worth saving; skip the graceful 'q'
    await process.wait()


async def _drain_stderr(recorder: Recorder) -> None:
    assert recorder.process.stderr
    async for raw in recorder.process.stderr:
        line = raw.decode(errors="replace").strip()
        if line:
            recorder.stderr_tail.append(line)
            del recorder.stderr_tail[:-20]
            log.info("ffmpeg[%s] %s", recorder.session_id, line)


async def stop(session_id: str) -> Recorder:
    recorder = _recorders.pop(session_id, None)
    if recorder is None:
        raise CaptureError("No active recording for this session.")

    process = recorder.process
    if process.returncode is None:
        try:
            # 'q' makes ffmpeg finalize the WAV headers. SIGKILL leaves the
            # RIFF length fields unwritten and the file unreadable.
            process.stdin.write(b"q")
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            log.warning("ffmpeg did not exit cleanly for %s; terminating", session_id)
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    if not recorder.full_wav.exists() or recorder.full_wav.stat().st_size < 1024:
        raise CaptureError(
            "Recording produced no audio. Check that the selected device is "
            "actually receiving sound."
        )

    return recorder
