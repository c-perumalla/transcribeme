"""Provisional live transcription of capture segments.

A segment is only safe to read once the *next* one exists — ffmpeg's segment
muxer writes sequentially, so the appearance of N+1 is the completion signal
for N. The final segment is flushed by `finish()` after ffmpeg exits.

Everything produced here is provisional: chunk boundaries clip words and there
is no diarization. The post-stop pass replaces it wholesale.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from . import asr, audio, config, events

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0
# Whisper uses the tail of the previous chunk to stay coherent across the cut.
PROMPT_TAIL_CHARS = 200


class LiveTranscriber:
    def __init__(self, session_id: str, segment_dir: Path) -> None:
        self.session_id = session_id
        self.segment_dir = segment_dir
        self.processed: set[Path] = set()
        self.prompt_tail = ""
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def finish(self) -> None:
        """Stop polling, then drain whatever segments remain."""
        self._stopping.set()
        if self._task:
            await self._task
        await self._drain(include_last=True)

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._drain(include_last=False)
            except Exception:
                log.exception("Live transcription pass failed for %s", self.session_id)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _drain(self, *, include_last: bool) -> None:
        segments = sorted(self.segment_dir.glob("*.wav"))
        # Without include_last, the newest file is still being written.
        candidates = segments if include_last else segments[:-1]

        for segment in candidates:
            if segment in self.processed:
                continue
            self.processed.add(segment)
            try:
                await self._transcribe_segment(segment)
            except Exception as exc:
                log.warning("Skipping live segment %s: %s", segment.name, exc)

    async def _transcribe_segment(self, segment: Path) -> None:
        index = int(segment.stem)
        offset = index * config.SEGMENT_SECONDS

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, self._blocking_transcribe, segment, offset
        )

        for channel, words in results:
            text = " ".join(w.text for w in words).strip()
            if not text:
                continue
            self.prompt_tail = (self.prompt_tail + " " + text)[-PROMPT_TAIL_CHARS:]
            events.publish(self.session_id, {
                "type": "partial",
                "channel": channel,
                "speaker": config.MY_NAME if channel == "me" else (
                    "Far end" if channel == "them" else "Live"
                ),
                "start": words[0].start,
                "end": words[-1].end,
                "text": text,
            })

    def _blocking_transcribe(self, segment: Path, offset: float) -> list[tuple]:
        """Runs off the event loop. Splits channels so live text is already
        attributed to you vs the far end without any diarization."""
        work_dir = segment.parent / f".{segment.stem}"
        try:
            tracks = audio.split_channels(segment, work_dir)
        except audio.AudioError:
            tracks = {}

        results: list[tuple] = []
        for channel in ("me", "them", "mixed"):
            wav = tracks.get(channel)
            if wav is None:
                continue
            words = asr.transcribe(
                wav, channel=channel, offset=offset,
                initial_prompt=self.prompt_tail or None,
            )
            if words:
                results.append((channel, words))
        return results
