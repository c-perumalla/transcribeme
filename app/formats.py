"""Render utterances as Markdown, plain text, SRT, or JSON."""

from __future__ import annotations

import json


def _clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _srt_clock(seconds: float) -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{_clock(seconds)},{millis:03d}"


def to_markdown(utterances: list[dict]) -> str:
    return "\n\n".join(
        f"**{u['speaker']}** [{_clock(u['start'])}]  \n{u['text']}"
        for u in utterances
    )


def to_text(utterances: list[dict]) -> str:
    return "\n\n".join(
        f"[{_clock(u['start'])}] {u['speaker']}: {u['text']}" for u in utterances
    )


def to_srt(utterances: list[dict]) -> str:
    blocks = []
    for i, u in enumerate(utterances, start=1):
        blocks.append(
            f"{i}\n"
            f"{_srt_clock(u['start'])} --> {_srt_clock(u['end'])}\n"
            f"{u['speaker']}: {u['text']}\n"
        )
    return "\n".join(blocks)


def to_json(utterances: list[dict]) -> str:
    return json.dumps(utterances, indent=2, ensure_ascii=False)


TRANSCRIPT_RENDERERS = {
    "md": to_markdown,
    "txt": to_text,
    "srt": to_srt,
    "json": to_json,
}


def bundle(session: dict, utterances: list[dict]) -> str:
    """One self-contained Markdown document: metadata, summary, transcript.

    This is what lands in the Drive folder, so a Claude Project gets a single
    coherent file per session rather than scattered fragments.
    """
    speakers = sorted({u["speaker"] for u in utterances})
    duration = session.get("duration_s") or 0
    lines = [
        "---",
        f"title: {session.get('title', 'Untitled')}",
        f"date: {session.get('created_at', '')}",
        f"duration: {_clock(duration)}",
        f"participants: {', '.join(speakers) if speakers else 'unknown'}",
        f"skill: {session.get('skill_name') or 'none'}",
        f"source: {session.get('source', 'unknown')}",
        "---",
        "",
        f"# {session.get('title', 'Untitled')}",
        "",
    ]

    summary = (session.get("summary_md") or "").strip()
    if summary:
        lines += ["## Summary", "", summary, "", "---", ""]

    lines += ["## Transcript", "", to_markdown(utterances), ""]
    return "\n".join(lines)
