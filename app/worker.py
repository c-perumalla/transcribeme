"""One background thread that runs every transcription job in FIFO order.

Whisper and pyannote each saturate the CPU on their own; two at once just
thrash and both finish later. Uploads, stopped recordings and batch files all
queue here instead of running concurrently in the web server's thread pool.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

_jobs: queue.Queue = queue.Queue()
_pending: list[str] = []          # job keys, in queue order, for "#N in queue"
_lock = threading.Lock()
_thread: threading.Thread | None = None


def submit(key: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> int:
    """Queue a job; return its 1-based position (1 = runs next)."""
    global _thread
    with _lock:
        _pending.append(key)
        position = len(_pending)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="transcribe-worker", daemon=True)
            _thread.start()
    _jobs.put((key, fn, args, kwargs))
    return position


def position(key: str) -> int | None:
    """1-based position among jobs not yet started, or None."""
    with _lock:
        return _pending.index(key) + 1 if key in _pending else None


def _run() -> None:
    while True:
        key, fn, args, kwargs = _jobs.get()
        with _lock:
            if key in _pending:
                _pending.remove(key)
        try:
            fn(*args, **kwargs)
        except Exception:  # jobs record their own failures; this is a backstop
            log.exception("Job %s crashed", key)
        finally:
            _jobs.task_done()
