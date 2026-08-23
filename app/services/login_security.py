import hashlib
import hmac
import secrets
import threading
from collections import deque
from datetime import timedelta
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import settings
from app.services.database import database, database_configured, utc_now


_LOCK = threading.Lock()
_LOCAL_LIMITS: dict[str, dict[str, Any]] = {}
_LOCAL_EVENTS: deque[dict[str, Any]] = deque(maxlen=100)
_LOCAL_BLOCKED: dict[str, dict[str, Any]] = {}
LOGIN_DEVICE_COOKIE = "instagram_studio_device"


def _device_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(settings.app_secret_key, salt="studio-device")


def _request_device_id(request: Request) -> str:
    existing = getattr(request.state, "studio_device_id", "")
    if existing:
        return existing
    raw_cookie = request.cookies.get(LOGIN_DEVICE_COOKIE, "")
    try:
        device_id = str(_device_serializer().loads(raw_cookie))
        if len(device_id) < 24:
            raise ValueError("Identifiant trop court.")
    except (BadSignature, ValueError, TypeError):
        device_id = secrets.token_urlsafe(24)
        request.state.studio_device_cookie = _device_serializer().dumps(device_id)
    request.state.studio_device_id = device_id
    return device_id


def attach_login_device_cookie(request: Request, response: Response) -> None:
    value = getattr(request.state, "studio_device_cookie", "")
    if not value:
        return
    response.set_cookie(
        key=LOGIN_DEVICE_COOKIE,
        value=value,
        max_age=31536000,
        httponly=True,
        secure=settings.studio_cookie_secure,
        samesite="lax",
        path="/",
    )


def _client_hash(device_id: str, device: str, browser: str) -> str:
    identity = f"{device_id}|{device}|{browser}".encode("utf-8", errors="replace")
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
    user_agent = request.headers.get("user-agent", "")[:500]
    device, browser = _device_and_browser(user_agent)
    return {
        "client_hash": _client_hash(_request_device_id(request), device, browser),
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
    if device_is_manually_blocked(client_hash):
        return {
            "allowed": False,
            "block_type": "manual",
            "retry_after_seconds": 0,
            "remaining_attempts": 0,
        }
    state = _read_limit(client_hash) or {}
    locked_until = state.get("locked_until")
    if locked_until and locked_until > now:
        return {
            "allowed": False,
            "block_type": "temporary",
            "retry_after_seconds": max(1, int((locked_until - now).total_seconds())),
            "remaining_attempts": 0,
        }
    count = int(state.get("attempts", 0))
    return {
        "allowed": True,
        "block_type": "",
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
        "identity_version": 2,
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
        "block_type": "temporary" if locked_until else "",
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


def device_is_manually_blocked(client_hash: str) -> bool:
    if database_configured():
        try:
            return (
                database().blocked_devices.count_documents(
                    {"_id": client_hash}, limit=1
                )
                > 0
            )
        except Exception:
            pass
    with _LOCK:
        return client_hash in _LOCAL_BLOCKED


def _device_is_known(client_hash: str) -> bool:
    if database_configured():
        try:
            return (
                database().login_events.count_documents(
                    {"client_hash": client_hash, "identity_version": 2}, limit=1
                )
                > 0
            )
        except Exception:
            pass
    with _LOCK:
        return any(
            event.get("client_hash") == client_hash
            and event.get("identity_version") == 2
            for event in _LOCAL_EVENTS
        )


def set_device_blocked(client_hash: str, blocked: bool) -> None:
    if (
        len(client_hash) != 64
        or any(character not in "0123456789abcdef" for character in client_hash)
        or not _device_is_known(client_hash)
    ):
        raise ValueError("Appareil inconnu ou identifiant invalide.")
    now = utc_now()
    if database_configured():
        try:
            if blocked:
                database().blocked_devices.update_one(
                    {"_id": client_hash},
                    {
                        "$set": {"blocked_at": now, "updated_at": now},
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                )
            else:
                database().blocked_devices.delete_one({"_id": client_hash})
                database().login_rate_limits.delete_one({"_id": client_hash})
            return
        except Exception:
            pass
    with _LOCK:
        if blocked:
            _LOCAL_BLOCKED[client_hash] = {"blocked_at": now, "updated_at": now}
        else:
            _LOCAL_BLOCKED.pop(client_hash, None)
            _LOCAL_LIMITS.pop(client_hash, None)


def _login_documents(limit: int) -> list[dict[str, Any]]:
    if database_configured():
        try:
            return list(
                database().login_events.find({}).sort("created_at", -1).limit(limit)
            )
        except Exception:
            pass
    with _LOCK:
        return [dict(item) for item in list(_LOCAL_EVENTS)[:limit]]


def _security_states(client_hashes: list[str]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    if database_configured() and client_hashes:
        try:
            for item in database().login_rate_limits.find(
                {"_id": {"$in": client_hashes}}
            ):
                states[str(item["_id"])] = dict(item)
            for item in database().blocked_devices.find(
                {"_id": {"$in": client_hashes}}
            ):
                states.setdefault(str(item["_id"]), {})["manually_blocked"] = True
                states[str(item["_id"])]["blocked_at"] = item.get("blocked_at")
            return states
        except Exception:
            pass
    with _LOCK:
        for client_hash in client_hashes:
            state = dict(_LOCAL_LIMITS.get(client_hash) or {})
            if client_hash in _LOCAL_BLOCKED:
                state["manually_blocked"] = True
                state["blocked_at"] = _LOCAL_BLOCKED[client_hash].get("blocked_at")
            states[client_hash] = state
    return states


def list_login_security(
    *, current_client_hash: str = "", limit: int = 30
) -> dict[str, list[dict[str, Any]]]:
    maximum = min(max(int(limit), 1), 50)
    documents = _login_documents(max(maximum, 50))
    client_hashes = list(
        dict.fromkeys(
            str(item.get("client_hash", ""))
            for item in documents
            if item.get("client_hash")
        )
    )
    states = _security_states(client_hashes)
    now = utc_now()

    def public_status(client_hash: str) -> dict[str, Any]:
        state = states.get(client_hash) or {}
        locked_until = state.get("locked_until")
        manually_blocked = bool(state.get("manually_blocked"))
        temporary = bool(locked_until and locked_until > now)
        return {
            "blocked": manually_blocked or temporary,
            "block_type": "manual" if manually_blocked else "temporary" if temporary else "",
            "locked_until": locked_until.isoformat() if temporary else None,
        }

    events = []
    devices: dict[str, dict[str, Any]] = {}
    for item in documents:
        created_at = item.get("created_at")
        client_hash = str(item.get("client_hash", ""))
        if not created_at or not client_hash:
            continue
        status = public_status(client_hash)
        public_event = {
            "success": bool(item.get("success")),
            "device": str(item.get("device") or "Appareil inconnu"),
            "browser": str(item.get("browser") or "Navigateur inconnu"),
            "created_at": created_at.isoformat(),
            "device_key": client_hash,
            "manageable": item.get("identity_version") == 2,
            **status,
        }
        if len(events) < maximum:
            events.append(public_event)
        if client_hash not in devices:
            devices[client_hash] = {
                "device_key": client_hash,
                "device": public_event["device"],
                "browser": public_event["browser"],
                "last_seen_at": public_event["created_at"],
                "current": client_hash == current_client_hash,
                "manageable": public_event["manageable"],
                **status,
            }
    return {"events": events, "devices": list(devices.values())}


def list_login_history(limit: int = 30) -> list[dict[str, Any]]:
    return list_login_security(limit=limit)["events"]


def reset_local_login_security() -> None:
    """Réinitialisation réservée aux tests automatisés."""
    with _LOCK:
        _LOCAL_LIMITS.clear()
        _LOCAL_EVENTS.clear()
        _LOCAL_BLOCKED.clear()
