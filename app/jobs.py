"""The accurate post-stop pipeline: split -> transcribe -> diarize -> align ->
summarize -> deliver.

Blocking by design (Whisper and pyannote are CPU/GPU-bound), so callers run it
in a worker thread. Progress lands in SQLite and on the SSE stream so the
browser can follow along either way.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from . import align, asr, audio, config, db, delivery, diarize, events, summarize

log = logging.getLogger(__name__)


ProgressFn = Callable[[str, float, str], None]


class _Reporter:
    """Fans stage progress out to SQLite, the SSE stream, and an optional
    callback (the CLI's progress bars).

    Sub-stage updates from Whisper and pyannote arrive many times a second on
    long files, so persisting is throttled to whole-percent changes.
    """

    def __init__(self, session_id: str, callback: ProgressFn | None) -> None:
        self.session_id = session_id
        self.callback = callback
        self._last_saved = -1.0

    def stage(self, stage: str, progress: float, note: str = "") -> None:
        if self.callback:
            self.callback(stage, progress, note)
        if abs(progress - self._last_saved) < 0.01 and progress < 1.0:
            return
        self._last_saved = progress
        db.set_stage(self.session_id, stage, progress)
        events.publish(self.session_id, {
            "type": "stage", "stage": stage, "progress": progress, "note": note,
        })

    def span(self, stage: str, start: float, end: float, note: str = ""):
        """A 0-1 callback mapped onto [start, end] of the overall bar."""
        self.stage(stage, start, note)
        return lambda fraction: self.stage(
            stage, start + (end - start) * fraction, note
        )


def _stage(session_id: str, stage: str, progress: float, note: str = "") -> None:
    _Reporter(session_id, None).stage(stage, progress, note)


def process(
    session_id: str,
    source_audio: Path,
    *,
    skill_id: str | None,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    dual_channel: bool = False,
    deliver: bool = True,
    progress: ProgressFn | None = None,
) -> bool:
    """Run the pipeline; return True on success. Failures are recorded on the
    session rather than raised, so one bad file never takes down a batch."""
    try:
        _process(
            session_id, source_audio, skill_id=skill_id,
            num_speakers=num_speakers, min_speakers=min_speakers,
            max_speakers=max_speakers, dual_channel=dual_channel,
            deliver=deliver, reporter=_Reporter(session_id, progress),
        )
        return True
    except Exception as exc:
        log.exception("Pipeline failed for %s", session_id)
        db.fail(session_id, str(exc))
        events.publish(session_id, {"type": "error", "message": str(exc)})
        return False


def _prepare_tracks(source_audio: Path, work_dir: Path, dual_channel: bool,
                    info: dict) -> dict[str, Path]:
    """Only live captures carry a meaningful mic/far-end channel split. Any
    file source — a stereo MP3, a 5.1 movie — is downmixed and diarized."""
    if dual_channel:
        return audio.split_channels(source_audio, work_dir / "tracks")
    already_prepared = (
        source_audio.suffix.lower() == ".wav"
        and info["channels"] == 1 and info["sample_rate"] == 16000
    )
    if already_prepared:
        return {"mixed": source_audio}
    return {"mixed": audio.normalize(source_audio, work_dir / "tracks" / "mixed.wav")}


def _process(
    session_id: str,
    source_audio: Path,
    *,
    skill_id: str | None,
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
    dual_channel: bool,
    deliver: bool,
    reporter: _Reporter,
) -> None:
    work_dir = source_audio.parent
    db.update(session_id, status="processing", error=None)
    speakers = dict(num_speakers=num_speakers, min_speakers=min_speakers,
                    max_speakers=max_speakers)

    # 1. Probe and split -------------------------------------------------
    reporter.stage("normalizing", 0.02)
    info = audio.probe(source_audio)
    db.update(
        session_id, duration_s=info["duration_s"], channels=info["channels"]
    )
    tracks = _prepare_tracks(source_audio, work_dir, dual_channel, info)
    dual = "me" in tracks and "them" in tracks

    # 2. Transcribe ------------------------------------------------------
    all_utterances: list[dict] = []

    if dual:
        # The mic track is physically separated, so it needs no diarization —
        # unless several people share the mic in an in-person meeting.
        me_words = asr.transcribe(tracks["me"], channel="me", on_progress=reporter.span(
            "transcribing", 0.05, 0.30, "your channel"))
        them_words = asr.transcribe(tracks["them"], channel="them", on_progress=reporter.span(
            "transcribing", 0.30, 0.55, "far-end channel"))

        them_turns = diarize.run(
            tracks["them"], **speakers, on_progress=reporter.span(
                "diarizing", 0.55, 0.85, "separating remote speakers"),
        ) if them_words else []

        if config.DIARIZE_MIC_CHANNEL and me_words:
            me_turns = diarize.run(tracks["me"])
            me_labelled = align.assign_speakers(me_words, me_turns)
            me_labelled = [
                (w, f"{config.MY_NAME} side: {s}") for w, s in me_labelled
            ]
        else:
            me_labelled = [(w, config.MY_NAME) for w in me_words]

        them_labelled = align.assign_speakers(them_words, them_turns)

        reporter.stage("aligning", 0.86)
        all_utterances = align.merge_timelines(
            align.to_utterances(me_labelled),
            align.to_utterances(them_labelled),
        )
    else:
        words = asr.transcribe(tracks["mixed"], channel="mixed", on_progress=reporter.span(
            "transcribing", 0.05, 0.55))
        turns = diarize.run(
            tracks["mixed"], **speakers,
            on_progress=reporter.span("diarizing", 0.55, 0.85),
        ) if words else []
        reporter.stage("aligning", 0.86)
        all_utterances = align.to_utterances(align.assign_speakers(words, turns))

    for utterance in all_utterances:
        utterance["speaker_id"] = utterance["speaker"]

    db.save_utterances(session_id, all_utterances)
    events.publish(session_id, {"type": "transcript", "utterances": all_utterances})

    if not all_utterances:
        db.update(session_id, status="done", stage="complete", progress=1.0,
                  error="No speech detected in this recording.")
        reporter.stage("complete", 1.0)
        events.publish(session_id, {"type": "done"})
        return

    # 3. Summarize -------------------------------------------------------
    if skill_id:
        reporter.stage("summarizing", 0.88, f"skill: {skill_id}")
        session = db.get(session_id)
        summary = summarize.run(session, all_utterances, skill_id)
        db.update(session_id, summary_md=summary, skill_name=skill_id)
        events.publish(session_id, {"type": "summary", "markdown": summary})

    # 4. Deliver ---------------------------------------------------------
    if deliver:
        reporter.stage("delivering", 0.96)
        session = db.get(session_id)
        drive_path = delivery.write(session, all_utterances)
        db.update(session_id, drive_path=drive_path)
    else:
        drive_path = None

    db.update(session_id, status="done", stage="complete", progress=1.0)
    reporter.stage("complete", 1.0)
    events.publish(session_id, {"type": "done", "drive_path": drive_path})
    log.info("Session %s complete (%d utterances)", session_id, len(all_utterances))


def resummarize(session_id: str, skill_id: str) -> None:
    """Re-run only the summary against a different skill — no re-transcription."""
    try:
        session = db.get(session_id)
        if not session or not session["utterances"]:
            raise RuntimeError("No transcript to summarize.")

        db.update(session_id, status="processing", error=None)
        _stage(session_id, "summarizing", 0.5, f"skill: {skill_id}")

        summary = summarize.run(session, session["utterances"], skill_id)
        db.update(session_id, summary_md=summary, skill_name=skill_id)
        events.publish(session_id, {"type": "summary", "markdown": summary})

        session = db.get(session_id)
        drive_path = delivery.write(session, session["utterances"])
        db.update(session_id, drive_path=drive_path, status="done",
                  stage="complete", progress=1.0)
        events.publish(session_id, {"type": "done", "drive_path": drive_path})
    except Exception as exc:
        log.exception("Re-summarize failed for %s", session_id)
        db.fail(session_id, str(exc))
        events.publish(session_id, {"type": "error", "message": str(exc)})
