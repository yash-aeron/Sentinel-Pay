"""Event bus mechanics: fan-out, cleanup, overflow policy, SSE framing."""

from __future__ import annotations

import asyncio

from app.services.events import MAX_QUEUE_DEPTH, Event, EventBus


def test_event_sse_framing_carries_type_and_payload():
    event = Event(type="transaction.created", payload={"transaction": {"id": "t1"}})
    wire = event.to_sse()
    assert wire.startswith(f"id: {event.id}\n")
    assert "event: transaction.created\n" in wire
    assert '"id":"t1"' in wire
    assert wire.endswith("\n\n")


def test_publish_fans_out_to_every_subscriber():
    bus = EventBus()
    first, second = bus.subscribe(), bus.subscribe()
    bus.publish("review.decided", action="approve")
    assert first.qsize() == 1 and second.qsize() == 1
    assert first.get_nowait().type == "review.decided"
    assert second.get_nowait().type == "review.decided"


def test_unsubscribed_queues_stop_receiving():
    bus = EventBus()
    queue = bus.subscribe()
    bus.unsubscribe(queue)
    bus.publish("transaction.created")
    assert queue.empty()
    assert bus.subscriber_count == 0


def test_overflow_drops_the_oldest_event_not_the_newest():
    bus = EventBus()
    queue = bus.subscribe()
    for i in range(MAX_QUEUE_DEPTH):
        bus.publish("transaction.created", sequence=i)
    bus.publish("transaction.created", sequence=MAX_QUEUE_DEPTH)  # overflows

    assert queue.qsize() == MAX_QUEUE_DEPTH
    first = queue.get_nowait()
    assert first.payload["sequence"] == 1  # 0 was dropped, 1 is now at the front
    # The newest event went to the back: drain and check the tail.
    tail = [queue.get_nowait() for _ in range(queue.qsize())]
    assert tail[-1].payload["sequence"] == MAX_QUEUE_DEPTH
    assert [event.payload["sequence"] for event in tail] == list(range(2, MAX_QUEUE_DEPTH + 1))


def test_publish_with_no_subscribers_is_a_no_op():
    bus = EventBus()
    bus.publish("transaction.created")  # must not raise
    assert bus.subscriber_count == 0


def test_concurrent_subscribe_unsubscribe_is_safe():
    bus = EventBus()
    queues = [bus.subscribe() for _ in range(10)]
    for queue in queues[:5]:
        bus.unsubscribe(queue)
    bus.publish("review.decided")
    assert bus.subscriber_count == 5
    for queue in queues[:5]:
        assert queue.empty()
    for queue in queues[5:]:
        assert queue.qsize() == 1


async def test_subscriber_can_consume_asynchronously():
    bus = EventBus()
    queue = bus.subscribe()

    async def produce() -> None:
        await asyncio.sleep(0.01)
        bus.publish("transaction.created", transaction={"id": "t-9"})

    task = asyncio.create_task(produce())
    event = await queue.get()
    await task
    assert event.payload["transaction"]["id"] == "t-9"
