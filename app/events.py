"""Per-session pub/sub for the SSE stream."""

from __future__ import annotations

import asyncio
from collections import defaultdict

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_history: dict[str, list[dict]] = defaultdict(list)

MAX_HISTORY = 500


def subscribe(session_id: str) -> tuple[asyncio.Queue, list[dict]]:
    """Return a fresh queue plus everything already emitted for this session.

    Replaying history means a browser that connects late — or reconnects after
    the SSE drop — still sees the whole live transcript.
    """
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers[session_id].add(queue)
    return queue, list(_history[session_id])


def unsubscribe(session_id: str, queue: asyncio.Queue) -> None:
    _subscribers[session_id].discard(queue)
    if not _subscribers[session_id]:
        _subscribers.pop(session_id, None)


def publish(session_id: str, event: dict) -> None:
    history = _history[session_id]
    history.append(event)
    if len(history) > MAX_HISTORY:
        del history[: len(history) - MAX_HISTORY]

    for queue in list(_subscribers.get(session_id, ())):
        queue.put_nowait(event)


def clear(session_id: str) -> None:
    _history.pop(session_id, None)
