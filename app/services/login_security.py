import hashlib
import hmac
import threading
from collections import deque
from datetime import timedelta
from typing import Any

from fastapi import Request

from app.config import settings
from app.services.database import database, database_configured, utc_now


_LOCK = threading.Lock()
_LOCAL_LIMITS: dict[str, dict[str, Any]] = {}
_LOCAL_EVENTS: deque[dict[str, Any]] = deque(maxlen=100)


def _client_hash(host: str, user_agent: str) -> str:
    identity = f"{host}|{user_agent}".encode("utf-8", errors="replace")
    return hmac.new(
        settings.app_secret_key.encode("utf-8"),
        identity,
        hashlib.sha256,
    ).hexdigest()


def _device_and_browser(user_agent: str) -> tuple[str, str]:
    agent = user_agent.lower()
    if "iphone" in agent:
        device = "iPhone"
    elif "ipad" in agent:
        device = "iPad"
    elif "android" in agent:
        device = "Android"
    elif "macintosh" in agent or "mac os" in agent:
        device = "Mac"
    elif "windows" in agent:
        device = "Windows"
    else:
        device = "Appareil inconnu"

    if "crios" in agent or ("chrome" in agent and "edg" not in agent):
        browser = "Chrome"
    elif "fxios" in agent or "firefox" in agent:
        browser = "Firefox"
    elif "edg" in agent:
        browser = "Edge"
    elif "safari" in agent:
        browser = "Safari"
    else:
        browser = "Navigateur inconnu"
    return device, browser


def login_client_context(request: Request) -> dict[str, str]:
    host = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")[:500]
    device, browser = _device_and_browser(user_agent)
    return {
        "client_hash": _client_hash(host, user_agent),
        "device": device,
        "browser": browser,
    }


def _read_limit(client_hash: str) -> dict[str, Any] | None:
    if database_configured():
        try:
            return database().login_rate_limits.find_one({"_id": client_hash})
        except Exception:
            pass
    with _LOCK:
        value = _LOCAL_LIMITS.get(client_hash)
        return dict(value) if value else None


def login_attempt_status(client_hash: str) -> dict[str, Any]:
    now = utc_now()
    state = _read_limit(client_hash) or {}
    locked_until = state.get("locked_until")
    if locked_until and locked_until > now:
        return {
            "allowed": False,
            "retry_after_seconds": max(1, int((locked_until - now).total_seconds())),
            "remaining_attempts": 0,
        }
    count = int(state.get("attempts", 0))
    return {
        "allowed": True,
        "retry_after_seconds": 0,
        "remaining_attempts": max(0, settings.login_max_attempts - count),
    }


def _save_limit(client_hash: str, state: dict[str, Any]) -> None:
    if database_configured():
        try:
            database().login_rate_limits.replace_one(
                {"_id": client_hash},
                {"_id": client_hash, **state},
                upsert=True,
            )
            return
        except Exception:
            pass
    with _LOCK:
        _LOCAL_LIMITS[client_hash] = dict(state)


def _save_event(context: dict[str, str], success: bool) -> None:
    event = {
        "client_hash": context["client_hash"],
        "device": context["device"],
        "browser": context["browser"],
        "success": bool(success),
        "created_at": utc_now(),
    }
    if database_configured():
        try:
            database().login_events.insert_one(event)
            return
        except Exception:
            pass
    with _LOCK:
        _LOCAL_EVENTS.appendleft(event)


def record_login_failure(context: dict[str, str]) -> dict[str, Any]:
    now = utc_now()
    state = _read_limit(context["client_hash"]) or {}
    window_started_at = state.get("window_started_at")
    if (
        not window_started_at
        or window_started_at <= now - timedelta(minutes=settings.login_window_minutes)
    ):
        attempts = 1
        window_started_at = now
    else:
        attempts = int(state.get("attempts", 0)) + 1

    locked_until = None
    if attempts >= settings.login_max_attempts:
        locked_until = now + timedelta(minutes=settings.login_lockout_minutes)

    _save_limit(
        context["client_hash"],
        {
            "attempts": attempts,
            "window_started_at": window_started_at,
            "locked_until": locked_until,
            "updated_at": now,
        },
    )
    _save_event(context, False)
    return {
        "allowed": locked_until is None,
        "retry_after_seconds": (
            int((locked_until - now).total_seconds()) if locked_until else 0
        ),
        "remaining_attempts": max(0, settings.login_max_attempts - attempts),
    }


def record_login_success(context: dict[str, str]) -> None:
    if database_configured():
        try:
            database().login_rate_limits.delete_one({"_id": context["client_hash"]})
        except Exception:
            pass
    with _LOCK:
        _LOCAL_LIMITS.pop(context["client_hash"], None)
    _save_event(context, True)


def list_login_history(limit: int = 30) -> list[dict[str, Any]]:
    maximum = min(max(int(limit), 1), 50)
    documents: list[dict[str, Any]]
    if database_configured():
        try:
            documents = list(
                database().login_events.find(
                    {}, {"client_hash": 0}
                ).sort("created_at", -1).limit(maximum)
            )
        except Exception:
            documents = []
    else:
        with _LOCK:
            documents = list(_LOCAL_EVENTS)[:maximum]

    return [
        {
            "success": bool(item.get("success")),
            "device": str(item.get("device") or "Appareil inconnu"),
            "browser": str(item.get("browser") or "Navigateur inconnu"),
            "created_at": item["created_at"].isoformat(),
        }
        for item in documents
        if item.get("created_at")
    ]


def reset_local_login_security() -> None:
    """Réinitialisation réservée aux tests automatisés."""
    with _LOCK:
        _LOCAL_LIMITS.clear()
        _LOCAL_EVENTS.clear()
