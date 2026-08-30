import asyncio
import json

from app.services import realtime


class ConnectedRequest:
    async def is_disconnected(self):
        return False


def test_realtime_stream_sends_ready_and_calendar_signal_without_content(monkeypatch):
    monkeypatch.setattr(realtime, "database_configured", lambda: False)

    async def scenario():
        stream = realtime.realtime_event_stream(ConnectedRequest())
        try:
            assert await anext(stream) == "retry: 5000\n\n"
            ready = await anext(stream)
            assert "event: ready" in ready

            await realtime.publish_calendar_change(
                action="created",
                publication_id="publication-123",
                status="scheduled",
            )
            message = await asyncio.wait_for(anext(stream), timeout=1)
            return message
        finally:
            await stream.aclose()

    message = asyncio.run(scenario())
    payload = json.loads(message.split("data: ", 1)[1])

    assert "event: calendar" in message
    assert payload["publication_id"] == "publication-123"
    assert payload["status"] == "scheduled"
    assert set(payload) == {
        "scope",
        "action",
        "publication_id",
        "status",
        "occurred_at",
        "revision",
    }
    assert realtime.realtime_subscriber_count() == 0


def test_realtime_queue_keeps_only_latest_burst(monkeypatch):
    monkeypatch.setattr(realtime, "database_configured", lambda: False)

    async def scenario():
        queue = realtime.subscribe_realtime()
        try:
            await realtime.publish_calendar_change(
                action="created",
                publication_id="one",
                status="scheduled",
            )
            await realtime.publish_calendar_change(
                action="rescheduled",
                publication_id="two",
                status="scheduled",
            )
            return queue.get_nowait()
        finally:
            realtime.unsubscribe_realtime(queue)

    event = asyncio.run(scenario())

    assert event["action"] == "rescheduled"
    assert event["publication_id"] == "two"
