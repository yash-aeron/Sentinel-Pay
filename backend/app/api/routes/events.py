"""Server-sent events stream for the analyst dashboard.

Every payment attempt and every review decision is pushed here the moment it
commits. The same analyst token guards this stream as the review endpoints,
because the feed carries risk features.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import AnalystDep, EventsDep
from app.services.events import EventBus

router = APIRouter(tags=["events"])


async def _event_generator(bus: EventBus) -> AsyncIterator[str]:
    queue = bus.subscribe()
    try:
        # reconnection hint for EventSource, then an explicit hello.
        yield "retry: 3000\n\n"
        yield "event: connected\ndata: {}\n\n"
        while True:
            event = await queue.get()
            yield event.to_sse()
    except asyncio.CancelledError:
        raise
    finally:
        bus.unsubscribe(queue)


@router.get("/events", responses={401: {"description": "Missing or wrong X-Analyst-Token"}})
async def stream_events(
    events_bus: EventsDep, _analyst: AnalystDep
) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(events_bus),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router"]
