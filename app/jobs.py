"""The accurate post-stop pipeline: split -> transcribe -> diarize -> align ->
summarize -> deliver.

Blocking by design (Whisper and pyannote are CPU/GPU-bound), so callers run it
in a worker thread. Progress lands in SQLite and on the SSE stream so the
browser can follow along either way.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import align, asr, audio, config, db, delivery, diarize, events, summarize

log = logging.getLogger(__name__)


def _stage(session_id: str, stage: str, progress: float, note: str = "") -> None:
    db.set_stage(session_id, stage, progress)
    events.publish(session_id, {
        "type": "stage", "stage": stage, "progress": progress, "note": note,
    })


def process(
    session_id: str,
    source_audio: Path,
    *,
    skill_id: str | None,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> None:
    try:
        _process(
            session_id, source_audio, skill_id=skill_id,
            num_speakers=num_speakers, min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
    except Exception as exc:
        log.exception("Pipeline failed for %s", session_id)
        db.fail(session_id, str(exc))
        events.publish(session_id, {"type": "error", "message": str(exc)})


def _process(
    session_id: str,
    source_audio: Path,
    *,
    skill_id: str | None,
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
) -> None:
    work_dir = source_audio.parent
    db.update(session_id, status="processing", error=None)

    # 1. Probe and split -------------------------------------------------
    _stage(session_id, "normalizing", 0.05)
    info = audio.probe(source_audio)
    db.update(
        session_id, duration_s=info["duration_s"], channels=info["channels"]
    )
    tracks = audio.split_channels(source_audio, work_dir / "tracks")
    dual = "me" in tracks and "them" in tracks

    # 2. Transcribe ------------------------------------------------------
    _stage(session_id, "transcribing", 0.15,
           "dual-channel" if dual else "single channel")

    all_utterances: list[dict] = []

    if dual:
        # The mic track is physically separated, so it needs no diarization —
        # unless several people share the mic in an in-person meeting.
        me_words = asr.transcribe(tracks["me"], channel="me")
        _stage(session_id, "transcribing", 0.40, "far-end channel")
        them_words = asr.transcribe(tracks["them"], channel="them")

        _stage(session_id, "diarizing", 0.65, "separating remote speakers")
        them_turns = diarize.run(
            tracks["them"], num_speakers=num_speakers,
            min_speakers=min_speakers, max_speakers=max_speakers,
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

        _stage(session_id, "aligning", 0.80)
        all_utterances = align.merge_timelines(
            align.to_utterances(me_labelled),
            align.to_utterances(them_labelled),
        )
    else:
        words = asr.transcribe(tracks["mixed"], channel="mixed")
        _stage(session_id, "diarizing", 0.60)
        turns = diarize.run(
            tracks["mixed"], num_speakers=num_speakers,
            min_speakers=min_speakers, max_speakers=max_speakers,
        ) if words else []
        _stage(session_id, "aligning", 0.80)
        all_utterances = align.to_utterances(align.assign_speakers(words, turns))

    for utterance in all_utterances:
        utterance["speaker_id"] = utterance["speaker"]

    db.save_utterances(session_id, all_utterances)
    events.publish(session_id, {"type": "transcript", "utterances": all_utterances})

    if not all_utterances:
        db.update(session_id, status="done", stage="complete", progress=1.0,
                  error="No speech detected in this recording.")
        events.publish(session_id, {"type": "done"})
        return

    # 3. Summarize -------------------------------------------------------
    if skill_id:
        _stage(session_id, "summarizing", 0.88, f"skill: {skill_id}")
        session = db.get(session_id)
        summary = summarize.run(session, all_utterances, skill_id)
        db.update(session_id, summary_md=summary, skill_name=skill_id)
        events.publish(session_id, {"type": "summary", "markdown": summary})

    # 4. Deliver ---------------------------------------------------------
    _stage(session_id, "delivering", 0.96)
    session = db.get(session_id)
    drive_path = delivery.write(session, all_utterances)
    db.update(session_id, drive_path=drive_path)

    db.update(session_id, status="done", stage="complete", progress=1.0)
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
