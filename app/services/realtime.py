import asyncio
import json
import time
from contextlib import suppress
from typing import Any, AsyncIterator

from fastapi import Request
from pymongo import ReturnDocument

from app.services.database import database, database_configured, utc_now


REALTIME_STATE_ID = "calendar"
REALTIME_POLL_SECONDS = 3.0
REALTIME_KEEPALIVE_SECONDS = 20.0

_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
_subscriber_wakeup: asyncio.Event | None = None
_watcher_task: asyncio.Task | None = None
_persistence_tasks: set[asyncio.Task] = set()
_last_revision = 0


def _broadcast(event: dict[str, Any]) -> None:
    for queue in tuple(_subscribers):
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with suppress(asyncio.QueueFull):
            queue.put_nowait(event)


def subscribe_realtime() -> asyncio.Queue[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
    _subscribers.add(queue)
    if _subscriber_wakeup is not None:
        _subscriber_wakeup.set()
    return queue


def unsubscribe_realtime(queue: asyncio.Queue[dict[str, Any]]) -> None:
    _subscribers.discard(queue)
    if _subscriber_wakeup is not None:
        _subscriber_wakeup.set()


def realtime_subscriber_count() -> int:
    return len(_subscribers)


def _persist_event(event: dict[str, Any]) -> int | None:
    if not database_configured():
        return None
    document = database().realtime_state.find_one_and_update(
        {"_id": REALTIME_STATE_ID},
        {
            "$inc": {"revision": 1},
            "$set": {"event": event, "updated_at": utc_now()},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    revision = (document or {}).get("revision")
    return int(revision) if isinstance(revision, (int, float)) else None


async def _persist_and_track_revision(event: dict[str, Any]) -> None:
    global _last_revision
    try:
        revision = await asyncio.to_thread(_persist_event, event)
    except Exception:
        revision = None
    if revision is not None:
        _last_revision = max(_last_revision, revision)


async def publish_calendar_change(
    *,
    action: str,
    publication_id: Any,
    status: str = "",
) -> None:
    """Publie uniquement un signal d'invalidation, jamais le contenu du post."""
    event = {
        "scope": "calendar",
        "action": str(action or "updated")[:40],
        "publication_id": str(publication_id or "")[:80],
        "status": str(status or "")[:40],
        "occurred_at": utc_now().isoformat(),
    }
    local_revision = f"local-{time.time_ns()}"
    _broadcast({**event, "revision": local_revision})
    if database_configured():
        task = asyncio.create_task(
            _persist_and_track_revision(event),
            name="instagram-studio-realtime-persist",
        )
        _persistence_tasks.add(task)
        task.add_done_callback(_persistence_tasks.discard)


def _latest_persisted_event() -> tuple[int, dict[str, Any]] | None:
    if not database_configured():
        return None
    document = database().realtime_state.find_one(
        {"_id": REALTIME_STATE_ID},
        {"revision": 1, "event": 1},
    )
    if not document or not isinstance(document.get("revision"), (int, float)):
        return None
    event = document.get("event") or {}
    if not isinstance(event, dict):
        event = {}
    return int(document["revision"]), event


async def _watch_persisted_events() -> None:
    global _last_revision
    while True:
        wakeup = _subscriber_wakeup
        if wakeup is None:
            return
        if not _subscribers:
            wakeup.clear()
            if not _subscribers:
                await wakeup.wait()
            continue
        try:
            latest = await asyncio.to_thread(_latest_persisted_event)
            if latest:
                revision, event = latest
                if revision > _last_revision:
                    _last_revision = revision
                    _broadcast({**event, "scope": "calendar", "revision": revision})
        except Exception:
            # Le temps réel reste local si MongoDB est momentanément indisponible.
            pass
        wakeup.clear()
        try:
            await asyncio.wait_for(
                wakeup.wait(),
                timeout=REALTIME_POLL_SECONDS,
            )
        except asyncio.TimeoutError:
            pass


def start_realtime_watcher() -> None:
    global _subscriber_wakeup, _watcher_task
    if _watcher_task is None or _watcher_task.done():
        _subscriber_wakeup = asyncio.Event()
        _watcher_task = asyncio.create_task(
            _watch_persisted_events(),
            name="instagram-studio-realtime",
        )


async def stop_realtime_watcher() -> None:
    global _subscriber_wakeup, _watcher_task
    task = _watcher_task
    _watcher_task = None
    _subscriber_wakeup = None
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    for persistence_task in tuple(_persistence_tasks):
        persistence_task.cancel()
    if _persistence_tasks:
        await asyncio.gather(*tuple(_persistence_tasks), return_exceptions=True)


def _sse(event_name: str, payload: dict[str, Any], event_id: Any = "") -> str:
    lines = []
    if event_id != "":
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append(
        "data: "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    return "\n".join(lines) + "\n\n"


async def realtime_event_stream(request: Request) -> AsyncIterator[str]:
    queue = subscribe_realtime()
    try:
        yield "retry: 5000\n\n"
        yield _sse("ready", {"scope": "calendar", "revision": _last_revision})
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=REALTIME_KEEPALIVE_SECONDS,
                )
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield _sse("calendar", event, event.get("revision", ""))
    finally:
        unsubscribe_realtime(queue)
