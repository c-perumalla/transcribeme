"""Turn word timings + speaker turns into speaker-attributed utterances."""

from __future__ import annotations

from . import config
from .asr import Word
from .diarize import Turn


def assign_speakers(words: list[Word], turns: list[Turn]) -> list[tuple[Word, str]]:
    """Label each word with the diarization turn it overlaps most.

    Whisper word boundaries and pyannote turn boundaries never line up exactly,
    so maximum overlap is the right rule. A word that overlaps nothing (silence
    gaps, or speech pyannote dropped) falls back to the nearest turn by midpoint
    rather than being discarded.
    """
    if not turns:
        return [(w, "SPEAKER_00") for w in words]

    ordered = sorted(turns, key=lambda t: t.start)
    labelled: list[tuple[Word, str]] = []

    for word in words:
        best_label, best_overlap = None, 0.0
        for turn in ordered:
            if turn.start >= word.end:
                break
            overlap = min(word.end, turn.end) - max(word.start, turn.start)
            if overlap > best_overlap:
                best_label, best_overlap = turn.speaker, overlap

        if best_label is None:
            midpoint = (word.start + word.end) / 2
            best_label = min(
                ordered,
                key=lambda t: abs(midpoint - (t.start + t.end) / 2),
            ).speaker

        labelled.append((word, best_label))

    return labelled


def to_utterances(labelled: list[tuple[Word, str]]) -> list[dict]:
    """Collapse consecutive same-speaker words into utterances.

    Splits on a speaker change or a pause longer than UTTERANCE_GAP_S, so a
    single speaker's monologue still breaks into readable paragraphs.
    """
    utterances: list[dict] = []
    current: dict | None = None

    for word, speaker in sorted(labelled, key=lambda pair: pair[0].start):
        gap = word.start - current["end"] if current else 0.0
        if (
            current is None
            or speaker != current["speaker"]
            or gap > config.UTTERANCE_GAP_S
        ):
            if current:
                utterances.append(current)
            current = {
                "speaker": speaker,
                "channel": word.channel,
                "start": word.start,
                "end": word.end,
                "text": word.text,
            }
        else:
            current["end"] = word.end
            current["text"] += " " + word.text

    if current:
        utterances.append(current)
    return utterances


def merge_timelines(*groups: list[dict]) -> list[dict]:
    """Interleave utterances from separate channels into one timeline."""
    merged = [u for group in groups for u in group]
    merged.sort(key=lambda u: u["start"])
    return merged


def rename_speakers(utterances: list[dict], names: dict[str, str]) -> list[dict]:
    """Apply display names without losing the original diarization labels."""
    for utterance in utterances:
        original = utterance.get("speaker_id", utterance["speaker"])
        utterance["speaker_id"] = original
        utterance["speaker"] = names.get(original, original)
    return utterances
