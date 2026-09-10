"""Speaker diarization via pyannote.audio 4.x.

The pipeline is gated on HuggingFace: you must accept the model conditions and
supply a read token, or `from_pretrained` returns None rather than raising.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_pipeline = None
_lock = threading.Lock()


@dataclass
class Turn:
    start: float
    end: float
    speaker: str


class DiarizationError(RuntimeError):
    pass


def _load():
    global _pipeline
    with _lock:
        if _pipeline is not None:
            return _pipeline

        if not config.HUGGINGFACE_TOKEN:
            raise DiarizationError(
                "HUGGINGFACE_TOKEN is not set. Accept the model conditions at "
                f"https://huggingface.co/{config.DIARIZATION_MODEL} , create a "
                "read token, and put it in .env."
            )

        from pyannote.audio import Pipeline

        pipeline = Pipeline.from_pretrained(
            config.DIARIZATION_MODEL, token=config.HUGGINGFACE_TOKEN
        )
        if pipeline is None:
            raise DiarizationError(
                f"Could not load {config.DIARIZATION_MODEL}. This usually means the "
                "gated-model conditions have not been accepted for your account, or "
                "the token lacks read access. Visit "
                f"https://huggingface.co/{config.DIARIZATION_MODEL} and accept them."
            )

        _pipeline = _to_best_device(pipeline)
        return _pipeline


def _to_best_device(pipeline):
    """Prefer MPS, fall back to CPU.

    pyannote's MPS coverage is incomplete and varies by op; a silent, logged
    fallback is better than failing a recording the user already made.
    """
    import torch

    if torch.backends.mps.is_available():
        try:
            pipeline.to(torch.device("mps"))
            log.info("Diarization running on MPS (Metal)")
            return pipeline
        except Exception as exc:
            log.warning("MPS unavailable for diarization (%s); falling back to CPU", exc)

    pipeline.to(torch.device("cpu"))
    log.info("Diarization running on CPU")
    return pipeline


def run(
    wav: Path,
    *,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> list[Turn]:
    pipeline = _load()

    kwargs: dict = {}
    if num_speakers:
        kwargs["num_speakers"] = num_speakers
    else:
        if min_speakers:
            kwargs["min_speakers"] = min_speakers
        if max_speakers:
            kwargs["max_speakers"] = max_speakers

    output = pipeline(str(wav), **kwargs)
    annotation = _as_annotation(output)
    return [
        Turn(start=float(segment.start), end=float(segment.end), speaker=str(label))
        for segment, _track, label in annotation.itertracks(yield_label=True)
    ]


def _as_annotation(output):
    """Unwrap the pipeline result into a pyannote Annotation.

    pyannote 4.x returns a DiarizeOutput wrapper; 3.x returned the Annotation
    directly. Prefer `exclusive_speaker_diarization` — pyannote produces it
    specifically for downstream transcription because it removes overlapping
    speech turns, and overlapping turns make word-to-speaker assignment
    ambiguous exactly where two people talk at once.
    """
    for attribute in ("exclusive_speaker_diarization", "speaker_diarization"):
        annotation = getattr(output, attribute, None)
        if annotation is not None and hasattr(annotation, "itertracks"):
            return annotation

    if hasattr(output, "itertracks"):  # pyannote 3.x
        return output

    raise DiarizationError(
        f"Unexpected diarization result type {type(output).__name__}; "
        "this build of pyannote.audio is not supported."
    )
