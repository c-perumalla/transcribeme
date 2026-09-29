"""Command-line entry point: `transcribeme serve | batch | doctor`.

One launcher for macOS and Windows, replacing the bash-only pieces (lsof port
checks, diagnose_mic.sh) with Python that behaves the same everywhere.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="transcribeme")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the web app")
    serve.add_argument("--host", default=config.HOST)
    serve.add_argument("--port", type=int, default=config.PORT)

    b = sub.add_parser("batch", help="Transcribe every audio/video file in a folder")
    b.add_argument("folder", type=Path)
    b.add_argument("-r", "--recursive", action="store_true", help="Include subfolders")
    b.add_argument("--skill", help="Skill id to summarize with (e.g. meeting-notes)")
    b.add_argument("--speakers", type=int, help="Known speaker count for every file")
    b.add_argument("--formats", default=",".join(_batch().DEFAULT_FORMATS),
                   help="Comma list of md,srt,txt,json (default: md,srt)")
    b.add_argument("--force", action="store_true",
                   help="Re-process files whose outputs already exist")
    b.add_argument("--no-drive", action="store_true",
                   help="Don't copy results into the Google Drive folder")

    d = sub.add_parser("doctor", help="Check the environment is ready")
    d.add_argument("--mic", nargs="?", const="", metavar="DEVICE",
                   help="Also record 3s from a device (default: the first one)")
    d.add_argument("--offline", action="store_true",
                   help="Skip checks that need the network (model access)")
    d.add_argument("--download", action="store_true",
                   help="Download model weights now so the first job doesn't")

    args = parser.parse_args(argv)
    return {"serve": cmd_serve, "batch": cmd_batch, "doctor": cmd_doctor}[args.command](args)


def _batch():
    from . import batch
    return batch


# --- serve ------------------------------------------------------------------

def cmd_serve(args) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((args.host, args.port))
        except OSError:
            print(f"Port {args.port} on {args.host} is already in use.\n"
                  f"Set PORT in .env or pass --port, e.g.  transcribeme serve --port 8010")
            return 1

    import uvicorn

    print(f"TranscribeMe on http://{args.host}:{args.port}")
    # No reload/workers: those make uvicorn use a SelectorEventLoop on Windows,
    # which cannot spawn the ffmpeg capture subprocess.
    uvicorn.run("app.main:app", host=args.host, port=args.port, loop="asyncio")
    return 0


# --- batch ------------------------------------------------------------------

def cmd_batch(args) -> int:
    from rich.console import Console
    from rich.progress import (BarColumn, Progress, TaskProgressColumn, TextColumn,
                               TimeElapsedColumn)
    from rich.table import Table

    from . import db

    batch = _batch()
    console = Console()
    # The pipeline's own INFO logs would tear through the progress display.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    try:
        opts = batch.Options(
            folder=args.folder, recursive=args.recursive, skill=args.skill,
            num_speakers=args.speakers, formats=tuple(args.formats.split(",")),
            force=args.force, deliver=not args.no_drive,
        )
        with console.status(f"Scanning {opts.folder} ..."):
            bid = batch.create(opts, origin="cli")
    except batch.BatchError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    data = db.get_batch(bid)
    items = data["items"]
    todo = [i for i in items if i["status"] == "queued"]
    audio_total = sum(i["duration_s"] or 0 for i in todo)
    console.print(
        f"[bold]{opts.folder}[/bold]\n"
        f"[bold]{len(items)}[/bold] files  ·  "
        f"[bold]{len(todo)}[/bold] to process ({_hms(audio_total)} of audio)  ·  "
        f"{sum(i['status'] == 'skipped' for i in items)} already done  ·  "
        f"outputs → {batch.OUTPUT_DIRNAME}/"
    )
    for item in items:
        if item["status"] == "error":
            console.print(f"  [red]✗[/red] {item['rel_path']}  [dim]{item['error']}[/dim]")

    audio_done = 0.0
    started = time.monotonic()
    interrupted = False

    progress = Progress(
        TextColumn("{task.description}"), BarColumn(bar_width=30),
        TaskProgressColumn(), TextColumn("{task.fields[detail]}"),
        TimeElapsedColumn(), console=console, transient=False,
    )
    with progress:
        overall = progress.add_task("[bold]Batch", total=len(todo) or 1, detail="")
        current = progress.add_task("", total=1.0, detail="", visible=False)

        for n, item in enumerate(todo, start=1):
            duration = item["duration_s"] or 0.0
            name = _ellipsize(item["rel_path"], 40)
            progress.update(current, description=f"  {name}", completed=0,
                            detail="starting", visible=True)

            def on_progress(stage: str, fraction: float, note: str, _d=duration) -> None:
                progress.update(current, completed=fraction,
                                detail=f"{stage}{' · ' + note if note else ''}")
                progress.update(overall, detail=_eta(
                    audio_done + _d * fraction, audio_total, started))

            try:
                result = batch.run_item(bid, item["position"], opts, on_progress)
            except KeyboardInterrupt:
                interrupted = True
                progress.console.print(f"[yellow]■ interrupted[/yellow] {item['rel_path']}")
                break

            audio_done += duration
            progress.update(overall, completed=n,
                            detail=_eta(audio_done, audio_total, started))
            progress.console.print(_line(result, duration, opts.folder))

        progress.update(current, visible=False)
        wall = time.monotonic() - started
        if audio_done and not interrupted:
            progress.update(overall, detail=f"done · {audio_done / wall:.1f}× realtime")

    db.finish_batch(bid)
    final = db.get_batch(bid)
    _summary(console, Table, final, time.monotonic() - started, interrupted)
    failed = sum(i["status"] == "error" for i in final["items"])
    if interrupted:
        console.print("Re-run the same command to resume; finished files are skipped.")
        return 130
    return 1 if failed else 0


def _line(item: dict, duration: float, folder: Path) -> str:
    rel = item.get("rel_path", "?")
    if item.get("status") == "done":
        note = f"  [dim]({item['error']})[/dim]" if item.get("error") else ""
        outputs = json.loads(item.get("outputs_json") or "{}")
        out = next(iter(outputs.values()), "")
        out = Path(out).relative_to(folder).as_posix() if out else ""
        return f"[green]✓[/green] {rel}  [dim]{_hms(duration)} → {out}[/dim]{note}"
    return f"[red]✗[/red] {rel}  [red]{item.get('error') or 'failed'}[/red]"


def _summary(console, Table, batch: dict, wall_s: float, interrupted: bool) -> None:
    items = batch["items"]
    counts = {s: sum(i["status"] == s for i in items)
              for s in ("done", "skipped", "error", "queued")}
    table = Table(title="Batch summary", show_header=False, box=None, padding=(0, 2))
    table.add_row("Transcribed", f"[green]{counts['done']}[/green]")
    table.add_row("Skipped (already done)", str(counts["skipped"]))
    table.add_row("Failed", f"[red]{counts['error']}[/red]" if counts["error"] else "0")
    if interrupted:
        table.add_row("Not started", str(counts["queued"]))
    table.add_row("Wall time", _hms(wall_s))
    console.print()
    console.print(table)
    for item in items:
        if item["status"] == "error":
            console.print(f"  [red]✗[/red] {item['rel_path']}: {item['error']}")


def _eta(done_s: float, total_s: float, started: float) -> str:
    elapsed = time.monotonic() - started
    if done_s < 30 or elapsed < 5:
        return "measuring speed…"
    speed = done_s / elapsed
    return f"{speed:.1f}× realtime · ETA {_hms((total_s - done_s) / speed)}"


def _hms(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _ellipsize(text: str, width: int) -> str:
    return text if len(text) <= width else "…" + text[-(width - 1):]


# --- doctor -----------------------------------------------------------------

def cmd_doctor(args) -> int:
    failures = 0

    def check(label: str, ok: bool, detail: str = "", fatal: bool = True) -> None:
        nonlocal failures
        mark = "OK  " if ok else ("FAIL" if fatal else "WARN")
        print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
        if not ok and fatal:
            failures += 1

    print(f"Python {sys.version.split()[0]} on {sys.platform}")
    for tool in (config.FFMPEG, config.FFPROBE):
        try:
            out = subprocess.run([tool, "-version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace").stdout
            check(Path(tool).name, bool(out), out.splitlines()[0] if out else "no output")
        except OSError as exc:
            check(Path(tool).name, False,
                  f"{exc}. Run setup (setup.cmd on Windows) or install ffmpeg.")

    for module in ("torch", "pyannote.audio", "soundfile",
                   "mlx_whisper" if config.ASR_BACKEND == "mlx" else "faster_whisper"):
        try:
            __import__(module)
            check(f"import {module}", True)
        except Exception as exc:  # DLL load failures surface as OSError too
            hint = (" — install the Microsoft Visual C++ Redistributable (x64)"
                    if config.IS_WINDOWS and "DLL" in str(exc) else "")
            check(f"import {module}", False, f"{exc}{hint}")

    check("ASR backend", True, f"{config.ASR_BACKEND}, {config.CPU_THREADS} CPU threads")
    check("HUGGINGFACE_TOKEN set", bool(config.HUGGINGFACE_TOKEN),
          "" if config.HUGGINGFACE_TOKEN else "diarization will fail; see README")
    check("Claude credentials", bool(config.ANTHROPIC_API_KEY),
          "" if config.ANTHROPIC_API_KEY else
          "ANTHROPIC_API_KEY empty (fine if you used `ant auth login`); summaries need one",
          fatal=False)
    check("Drive folder", config.DRIVE_DIR.parent.exists(), str(config.DRIVE_DIR), fatal=False)

    if not args.offline and config.HUGGINGFACE_TOKEN:
        try:
            from . import diarize
            diarize._load()
            check("diarization model access", True, config.DIARIZATION_MODEL)
        except Exception as exc:
            check("diarization model access", False, str(exc))

    if args.download:
        failures += _download_asr(check)

    from . import devices
    try:
        found = devices.list_audio_devices()
        check("audio inputs", True, ", ".join(d["name"] for d in found))
    except Exception as exc:
        found = []
        check("audio inputs", False, str(exc), fatal=False)

    if args.mic is not None and found:
        _mic_test(args.mic or found[0]["name"], check)

    print("\nAll good." if not failures else f"\n{failures} problem(s) found.")
    return 1 if failures else 0


def _download_asr(check) -> int:
    from . import asr
    with tempfile.TemporaryDirectory() as tmp:
        silence = Path(tmp) / "silence.wav"
        subprocess.run([config.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=16000:cl=mono", "-t", "1", str(silence)], check=True)
        try:
            asr.transcribe(silence)
            check("ASR model downloaded", True)
            return 0
        except Exception as exc:
            check("ASR model downloaded", False, str(exc))
            return 1


def _mic_test(name: str, check) -> None:
    from . import devices
    args = devices.input_args(name)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "probe.wav"
        print(f"Recording 3s from {name!r} — say something...")
        proc = subprocess.run(
            [config.FFMPEG, "-hide_banner", "-loglevel", "error", *args, "-t", "3",
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        size = out.stat().st_size if out.exists() else 0
        if size <= 44:
            check("mic capture", False,
                  (proc.stderr.strip() or "no audio frames") + " — likely a "
                  "microphone privacy/permission block for the app running this")
            return
        level = subprocess.run(
            [config.FFMPEG, "-v", "info", "-i", str(out), "-af", "volumedetect",
             "-f", "null", "-"], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stderr
        peak = next((line.split("max_volume:")[1].strip()
                     for line in level.splitlines() if "max_volume" in line), "?")
        check("mic capture", True, f"{size} bytes, peak {peak}"
              + (" (silent: muted or wrong input?)" if peak.startswith("-9") else ""))


if __name__ == "__main__":
    sys.exit(main())
