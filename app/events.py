"""Per-session pub/sub for the SSE stream.

Publishers are mostly pipeline worker threads, while subscribers are asyncio
queues owned by the server's event loop. asyncio.Queue is not thread-safe, so a
cross-thread publish is handed to the owning loop with call_soon_threadsafe.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict

_lock = threading.Lock()
_subscribers: dict[str, dict[asyncio.Queue, asyncio.AbstractEventLoop]] = defaultdict(dict)
_history: dict[str, list[dict]] = defaultdict(list)

MAX_HISTORY = 500


def subscribe(session_id: str) -> tuple[asyncio.Queue, list[dict]]:
    """Return a fresh queue plus everything already emitted for this session.

    Replaying history means a browser that connects late — or reconnects after
    the SSE drop — still sees the whole live transcript.
    """
    queue: asyncio.Queue = asyncio.Queue()
    with _lock:
        _subscribers[session_id][queue] = asyncio.get_running_loop()
        return queue, list(_history[session_id])


def unsubscribe(session_id: str, queue: asyncio.Queue) -> None:
    with _lock:
        _subscribers[session_id].pop(queue, None)
        if not _subscribers[session_id]:
            _subscribers.pop(session_id, None)


def publish(session_id: str, event: dict) -> None:
    with _lock:
        history = _history[session_id]
        history.append(event)
        if len(history) > MAX_HISTORY:
            del history[: len(history) - MAX_HISTORY]
        targets = list(_subscribers.get(session_id, {}).items())

    for queue, loop in targets:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:  # loop already closed (server shutting down)
            pass


def clear(session_id: str) -> None:
    with _lock:
        _history.pop(session_id, None)
