"""In-process event bus feeding the analyst dashboard's SSE stream.

Deliberately simple: one asyncio queue per connected dashboard, publish after
commit. Multiple API workers would each duplicate the feed to their own
clients; the production answer is Postgres ``LISTEN/NOTIFY`` and that is
written down in ``docs/design.md`` as a Phase 5 concern rather than
discovered later.

Publishing never blocks and never raises: a slow dashboard drops that
dashboard's events rather than stalling the payment path. The queue is
bounded; on overflow the oldest pending event is dropped in favour of the
newest, because the dashboard's queue view is a re-fetch away from truth.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.core.time import utcnow

log = get_logger(__name__)

MAX_QUEUE_DEPTH = 500


@dataclass(frozen=True, slots=True)
class Event:
    type: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    at: datetime = field(default_factory=utcnow)

    def to_sse(self) -> str:
        import json

        data = json.dumps(
            {"id": self.id, "at": self.at.isoformat(), "type": self.type, **self.payload},
            separators=(",", ":"),
            default=str,
        )
        return f"id: {self.id}\nevent: {self.type}\ndata: {data}\n\n"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE_DEPTH)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event_type: str, **payload: Any) -> None:
        """Fan an event out to every subscriber. Never blocks or raises."""
        event = Event(type=event_type, payload=payload)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest pending event for this subscriber to make
                # room; a dashboard that far behind re-fetches state anyway.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
                log.warning("sse_subscriber_overflow", event_type=event_type)
        if not self._subscribers:
            log.debug("sse_event_unobserved", event_type=event_type)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


__all__ = ["Event", "EventBus", "MAX_QUEUE_DEPTH"]
