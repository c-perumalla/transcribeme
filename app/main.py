"""TranscribeMe — FastAPI app: capture, transcription, summaries, downloads."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from pydantic import BaseModel

from . import (audio, batch, capture, config, db, delivery, devices, events,
               formats, jobs, live, skills, worker)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("transcribeme")

app = FastAPI(title="TranscribeMe")

_live_transcribers: dict[str, live.LiveTranscriber] = {}


@app.on_event("startup")
def _startup() -> None:
    config.ensure_dirs()
    db.init()
    db.abandon_web_batches()
    log.info("ASR backend: %s | ffmpeg: %s | Drive folder: %s",
             config.ASR_BACKEND, config.FFMPEG, config.DRIVE_DIR)


@app.get("/api/info")
def api_info():
    return {
        "platform": "windows" if config.IS_WINDOWS else "mac" if config.IS_MAC else "other",
        "asr_backend": config.ASR_BACKEND,
    }


# --- Devices & skills -----------------------------------------------------

@app.get("/api/devices")
def api_devices():
    try:
        return {"devices": devices.list_audio_devices()}
    except devices.DeviceError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/skills")
def api_skills():
    return {"skills": skills.list_skills()}


# --- Live recording -------------------------------------------------------

@app.post("/api/recordings")
async def start_recording(
    device: str = Form(...),
    title: str = Form(""),
    skill: str = Form(""),
):
    title = title.strip() or f"Recording {datetime.now():%Y-%m-%d %H:%M}"
    session_id = db.create_session(
        title=title, source="live", skill_name=skill or None,
        device_name=device, status="recording",
    )
    try:
        recorder = await capture.start(session_id, device)
    except (capture.CaptureError, devices.DeviceError) as exc:
        db.fail(session_id, str(exc))
        raise HTTPException(400, str(exc)) from exc

    transcriber = live.LiveTranscriber(session_id, recorder.segment_dir)
    transcriber.start()
    _live_transcribers[session_id] = transcriber

    db.set_stage(session_id, "recording", 0.0)
    return {"id": session_id, "title": title, "device": device}


@app.post("/api/recordings/{session_id}/stop")
async def stop_recording(session_id: str):
    try:
        recorder = await capture.stop(session_id)
    except capture.CaptureError as exc:
        db.fail(session_id, str(exc))
        raise HTTPException(400, str(exc)) from exc

    transcriber = _live_transcribers.pop(session_id, None)
    if transcriber:
        await transcriber.finish()

    session = db.get(session_id)
    db.update(session_id, status="processing", stage="queued", progress=0.0)
    # Only a live capture has a meaningful mic / far-end channel split.
    worker.submit(session_id, jobs.process, session_id, recorder.full_wav,
                  skill_id=session["skill_name"], dual_channel=True)
    return {"id": session_id, "status": "processing"}


# --- Upload ---------------------------------------------------------------

@app.post("/api/sessions")
async def create_from_upload(
    file: UploadFile = File(...),
    title: str = Form(""),
    skill: str = Form(""),
    num_speakers: int | None = Form(None),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in audio.MEDIA_SUFFIXES:
        raise HTTPException(
            400,
            f"Unsupported file type {suffix or '(none)'}. Expected one of: "
            + ", ".join(sorted(audio.MEDIA_SUFFIXES)),
        )

    title = title.strip() or Path(file.filename or "Upload").stem
    session_id = db.create_session(
        title=title, source="upload", skill_name=skill or None,
        source_filename=file.filename,
    )

    destination = config.SESSIONS_DIR / session_id / f"source{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    worker.submit(session_id, _process_upload, session_id, destination,
                  skill or None, num_speakers)
    return {"id": session_id, "title": title, "status": "processing"}


def _process_upload(session_id: str, source: Path, skill_id: str | None,
                    num_speakers: int | None) -> None:
    """Extract the audio track, then run the diarized (single-channel) path.

    An uploaded video is deleted once its audio is out — the session keeps
    audio.wav, and a multi-GB copy of a video the user still has is dead weight.
    """
    db.set_stage(session_id, "extracting audio", 0.0)
    wav = source.with_name("audio.wav")
    try:
        audio.extract(source, wav)
    except audio.AudioError as exc:
        db.fail(session_id, str(exc))
        events.publish(session_id, {"type": "error", "message": str(exc)})
        return
    if audio.is_video(source):
        source.unlink(missing_ok=True)
    jobs.process(session_id, wav, skill_id=skill_id, num_speakers=num_speakers)


# --- Folder batches -------------------------------------------------------

class BatchRequest(BaseModel):
    folder: str
    recursive: bool = False
    skill: str | None = None
    num_speakers: int | None = None
    formats: list[str] = list(batch.DEFAULT_FORMATS)
    force: bool = False


@app.post("/api/batches")
def api_create_batch(req: BatchRequest):
    # Plain `def`: scanning and probing a large folder blocks, so FastAPI runs
    # this in its thread pool rather than on the event loop.
    try:
        opts = batch.Options(
            folder=Path(req.folder.strip().strip('"')), recursive=req.recursive,
            skill=req.skill or None, num_speakers=req.num_speakers,
            formats=tuple(req.formats), force=req.force,
        )
        bid = batch.create(opts, origin="web")
    except batch.BatchError as exc:
        raise HTTPException(400, str(exc)) from exc
    batch.enqueue(bid)
    return _batch_view(bid)


@app.get("/api/batches/latest")
def api_latest_batch():
    bid = db.latest_batch_id()
    return _batch_view(bid) if bid else {"batch": None}


@app.get("/api/batches/{bid}")
def api_get_batch(bid: str):
    return _batch_view(bid)


@app.post("/api/batches/{bid}/cancel")
def api_cancel_batch(bid: str):
    if not db.get_batch(bid):
        raise HTTPException(404, "No such batch")
    return {"cancelled": db.cancel_queued(bid), **_batch_view(bid)}


def _batch_view(bid: str) -> dict:
    data = db.get_batch(bid)
    if not data:
        raise HTTPException(404, "No such batch")
    return {"batch": data, "stats": batch.stats(data).__dict__}


# --- Sessions -------------------------------------------------------------

@app.get("/api/sessions")
def api_list_sessions():
    sessions = db.list_sessions()
    for session in sessions:
        session["queue_position"] = worker.position(session["id"])
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
def api_get_session(session_id: str):
    session = db.get(session_id)
    if not session:
        raise HTTPException(404, "No such session")
    return session


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str):
    if not db.get(session_id):
        raise HTTPException(404, "No such session")
    shutil.rmtree(config.SESSIONS_DIR / session_id, ignore_errors=True)
    db.delete(session_id)
    events.clear(session_id)
    return {"deleted": session_id}


@app.post("/api/sessions/{session_id}/summarize")
def api_resummarize(session_id: str, background: BackgroundTasks,
                    skill: str = Form(...)):
    session = db.get(session_id)
    if not session:
        raise HTTPException(404, "No such session")
    if not session["utterances"]:
        raise HTTPException(400, "This session has no transcript yet.")
    background.add_task(jobs.resummarize, session_id, skill)
    return {"id": session_id, "status": "processing"}


@app.patch("/api/sessions/{session_id}/speakers")
async def api_rename_speakers(session_id: str, payload: dict):
    from . import align

    session = db.get(session_id)
    if not session:
        raise HTTPException(404, "No such session")

    names = {str(k): str(v).strip() for k, v in (payload or {}).items() if str(v).strip()}
    utterances = align.rename_speakers(session["utterances"], names)

    db.save_utterances(session_id, utterances)
    db.save_speaker_names(session_id, names)

    # Keep the Drive copy in step with the renamed transcript.
    session = db.get(session_id)
    drive_path = delivery.write(session, utterances)
    db.update(session_id, drive_path=drive_path)
    return {"utterances": utterances}


# --- SSE ------------------------------------------------------------------

@app.get("/api/sessions/{session_id}/stream")
async def api_stream(session_id: str):
    queue, backlog = events.subscribe(session_id)

    async def generator():
        try:
            for event in backlog:
                yield {"data": json.dumps(event)}
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}  # keep the connection warm
                    continue
                yield {"data": json.dumps(event)}
        finally:
            events.unsubscribe(session_id, queue)

    return EventSourceResponse(generator())


# --- Downloads ------------------------------------------------------------

MEDIA_TYPES = {
    "md": "text/markdown", "txt": "text/plain",
    "srt": "application/x-subrip", "json": "application/json",
}


@app.get("/api/sessions/{session_id}/download")
def api_download(session_id: str, kind: str = "bundle", fmt: str = "md"):
    session = db.get(session_id)
    if not session:
        raise HTTPException(404, "No such session")
    utterances = session["utterances"]

    if kind == "transcript":
        renderer = formats.TRANSCRIPT_RENDERERS.get(fmt)
        if not renderer:
            raise HTTPException(400, f"Unsupported format {fmt!r}")
        body, suffix = renderer(utterances), fmt
    elif kind == "summary":
        if not session.get("summary_md"):
            raise HTTPException(404, "This session has no summary yet.")
        body, suffix = session["summary_md"], "md"
    elif kind == "bundle":
        body, suffix = formats.bundle(session, utterances), "md"
    else:
        raise HTTPException(400, f"Unknown kind {kind!r}")

    filename = f"{delivery.slug(session['title'], session_id)}-{kind}.{suffix}"
    return Response(
        content=body,
        media_type=MEDIA_TYPES.get(suffix, "text/plain"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/sessions/{session_id}/audio")
def api_audio(session_id: str):
    directory = config.SESSIONS_DIR / session_id
    for pattern in ("full.wav", "audio.wav", "source.*"):
        for candidate in sorted(directory.glob(pattern)):
            return FileResponse(candidate)
    raise HTTPException(404, "No audio stored for this session")


# --- Static ---------------------------------------------------------------

app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
