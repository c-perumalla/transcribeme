"""Transcribe every audio/video file in a folder.

Shared by the CLI (`transcribeme batch`) and the web UI's Folder tab. Each file
becomes an ordinary session, so batch results also show up in the Sessions
list, and its transcript is written to `<folder>/transcripts/`.

Re-running a batch skips files whose outputs already exist, which is what makes
an interrupted batch resumable. Outputs are written to a temp file and renamed,
so a crash never leaves a partial file that would be mistaken for a finished one.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import audio, config, db, formats, jobs, worker

log = logging.getLogger(__name__)

OUTPUT_DIRNAME = "transcripts"
ALL_FORMATS = ("md", "srt", "txt", "json")
DEFAULT_FORMATS = ("md", "srt")

ProgressFn = Callable[[str, float, str], None]


class BatchError(ValueError):
    pass


@dataclass
class Options:
    folder: Path
    recursive: bool = False
    skill: str | None = None
    num_speakers: int | None = None
    formats: tuple[str, ...] = DEFAULT_FORMATS
    force: bool = False
    deliver: bool = True

    def __post_init__(self) -> None:
        self.folder = Path(os.path.expanduser(str(self.folder))).resolve()
        self.formats = tuple(dict.fromkeys(f.strip().lower() for f in self.formats if f.strip()))
        unknown = [f for f in self.formats if f not in ALL_FORMATS]
        if unknown or not self.formats:
            raise BatchError(
                f"Unknown output format(s) {', '.join(unknown) or '(none)'}. "
                f"Choose from: {', '.join(ALL_FORMATS)}"
            )

    def as_dict(self) -> dict:
        d = asdict(self)
        d["folder"] = str(self.folder)
        d["formats"] = list(self.formats)
        return d


# --- Planning ---------------------------------------------------------------

def _natural_key(path: Path) -> list:
    """Sort 'part 2' before 'part 10'."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", path.as_posix())]


def scan(folder: Path, recursive: bool = False) -> list[Path]:
    """Media files under `folder`, skipping hidden files and our own outputs."""
    if not folder.is_dir():
        raise BatchError(f"Not a folder: {folder}")
    candidates = folder.rglob("*") if recursive else folder.iterdir()
    files = []
    for path in candidates:
        rel = path.relative_to(folder)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if OUTPUT_DIRNAME in rel.parts[:-1]:
            continue
        if path.is_file() and path.suffix.lower() in audio.MEDIA_SUFFIXES:
            files.append(path)
    return sorted(files, key=lambda p: _natural_key(p.relative_to(folder)))


def output_paths(folder: Path, file: Path, fmts: tuple[str, ...],
                 keep_suffix: bool = False) -> dict[str, Path]:
    """Where each output format for `file` goes, mirroring subfolders.

    `keep_suffix` disambiguates siblings that share a stem (talk.mp4 and
    talk.m4a would otherwise both claim talk.md).
    """
    rel = file.relative_to(folder)
    stem = rel.name if keep_suffix else rel.stem
    base = folder / OUTPUT_DIRNAME / rel.parent
    return {fmt: base / f"{stem}.{fmt}" for fmt in fmts}


def plan(opts: Options) -> list[dict]:
    """One entry per file: status 'queued', 'skipped' (outputs exist) or
    'error' (unreadable, e.g. a video with no audio track)."""
    files = scan(opts.folder, opts.recursive)
    stems = Counter((f.parent, f.stem.lower()) for f in files)

    items = []
    for file in files:
        outputs = output_paths(
            opts.folder, file, opts.formats,
            keep_suffix=stems[(file.parent, file.stem.lower())] > 1,
        )
        item = {
            "path": str(file),
            "rel_path": file.relative_to(opts.folder).as_posix(),
            "outputs": {k: str(v) for k, v in outputs.items()},
            "status": "queued",
            "duration_s": None,
        }
        if not opts.force and all(p.exists() for p in outputs.values()):
            item["status"] = "skipped"
        else:
            try:
                item["duration_s"] = audio.probe(file)["duration_s"]
            except audio.AudioError as exc:
                item["status"], item["error"] = "error", str(exc)
        items.append(item)
    return items


def create(opts: Options, origin: str) -> str:
    items = plan(opts)
    if not items:
        raise BatchError(
            f"No audio or video files found in {opts.folder}"
            + ("" if opts.recursive else " (subfolders not searched)")
        )
    return db.create_batch(str(opts.folder), opts.as_dict(), origin, items)


def options_from(batch: dict) -> Options:
    raw = dict(batch["options"])
    raw["formats"] = tuple(raw.get("formats") or DEFAULT_FORMATS)
    return Options(**raw)


# --- Running ----------------------------------------------------------------

def run_item(bid: str, position: int, opts: Options,
             progress: ProgressFn | None = None) -> dict:
    """Process one file. Never raises for a bad file — the failure is recorded
    on the item — but lets KeyboardInterrupt through after recording it."""
    item = db.get_batch_item(bid, position)
    if item is None or item["status"] != "queued":
        return item or {}  # cancelled while waiting in the queue

    src = Path(item["path"])
    sid = db.create_session(
        title=src.stem, source="batch", skill_name=opts.skill,
        source_filename=item["rel_path"],
    )
    db.update_batch_item(bid, position, status="running", session_id=sid,
                         started_at=True)
    try:
        if progress:
            progress("extracting audio", 0.0, "")
        db.set_stage(sid, "extracting audio", 0.0)
        wav = config.SESSIONS_DIR / sid / "audio.wav"
        try:
            audio.extract(src, wav)
        except audio.AudioError as exc:
            db.fail(sid, str(exc))
            return _finish(bid, position, status="error", error=str(exc))

        ok = jobs.process(
            sid, wav, skill_id=opts.skill, num_speakers=opts.num_speakers,
            dual_channel=False, deliver=opts.deliver, progress=progress,
        )
        session = db.get(sid) or {}
        if not ok:
            return _finish(bid, position, status="error",
                           error=session.get("error") or "Processing failed")

        outputs = {k: Path(v) for k, v in _outputs_of(item).items()}
        write_outputs(session, outputs)
        # "No speech detected" is stored as a note, not a failure.
        return _finish(bid, position, status="done", error=session.get("error"))
    except KeyboardInterrupt:
        db.fail(sid, "Interrupted")
        _finish(bid, position, status="error", error="Interrupted")
        raise
    except Exception as exc:  # e.g. an unwritable output folder
        log.exception("Batch item %s failed", item["rel_path"])
        db.fail(sid, str(exc))
        return _finish(bid, position, status="error", error=str(exc))


def _outputs_of(item: dict) -> dict[str, str]:
    raw = item.get("outputs_json") or "{}"
    return json.loads(raw) if isinstance(raw, str) else raw


def _finish(bid: str, position: int, **fields) -> dict:
    db.update_batch_item(bid, position, finished_at=True, **fields)
    return db.get_batch_item(bid, position) or {}


def write_outputs(session: dict, outputs: dict[str, Path]) -> None:
    utterances = session.get("utterances") or []
    for fmt, path in outputs.items():
        body = (formats.bundle(session, utterances) if fmt == "md"
                else formats.TRANSCRIPT_RENDERERS[fmt](utterances))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)


def enqueue(bid: str) -> None:
    """Hand a web batch's queued files to the shared worker, one job each, so
    uploads submitted meanwhile interleave instead of waiting for the lot."""
    batch = db.get_batch(bid)
    opts = options_from(batch)
    for item in batch["items"]:
        if item["status"] == "queued":
            worker.submit(f"batch:{bid}:{item['position']}",
                          run_item, bid, item["position"], opts)
    worker.submit(f"batch:{bid}:finish", db.finish_batch, bid)


# --- Reporting --------------------------------------------------------------

def _seconds_between(start: str | None, end: str | None) -> float:
    if not start:
        return 0.0
    t0 = datetime.fromisoformat(start)
    t1 = datetime.fromisoformat(end) if end else datetime.now(t0.tzinfo)
    return max(0.0, (t1 - t0).total_seconds())


@dataclass
class Stats:
    total: int = 0
    counts: dict = field(default_factory=dict)
    audio_total_s: float = 0.0
    audio_done_s: float = 0.0
    elapsed_s: float = 0.0
    eta_s: float | None = None
    finished: bool = False


def stats(batch: dict) -> Stats:
    """Counts plus an ETA from measured speed (audio seconds per wall second),
    which adapts to the machine instead of guessing a realtime factor."""
    items = batch["items"]
    s = Stats(total=len(items), counts=dict(Counter(i["status"] for i in items)))
    work = [i for i in items if i["status"] not in ("skipped", "cancelled")
            and i.get("duration_s")]
    s.audio_total_s = sum(i["duration_s"] for i in work)

    busy_s = 0.0
    for item in work:
        if item["status"] in ("done", "error") and item.get("finished_at"):
            s.audio_done_s += item["duration_s"]
            busy_s += _seconds_between(item["started_at"], item["finished_at"])
        elif item["status"] == "running":
            s.audio_done_s += item["duration_s"] * (item.get("progress") or 0.0)
            busy_s += _seconds_between(item["started_at"], None)

    starts = [i["started_at"] for i in items if i.get("started_at")]
    s.elapsed_s = _seconds_between(min(starts), batch.get("finished_at")) if starts else 0.0
    s.finished = not any(i["status"] in ("queued", "running") for i in items)
    if not s.finished and s.audio_done_s > 30 and busy_s > 0:
        s.eta_s = (s.audio_total_s - s.audio_done_s) * busy_s / s.audio_done_s
    return s
