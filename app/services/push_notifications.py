import asyncio
import json
from typing import Any

from pywebpush import WebPushException, webpush

from app.config import settings
from app.services.database import database, database_configured, utc_now


DEFAULT_PREFERENCES = {
    "before_publication": True,
    "published": True,
    "failed": True,
    "manual_music": True,
    "studio_login": True,
    "instagram_token": True,
}


def push_configured() -> bool:
    return bool(
        settings.vapid_public_key
        and settings.vapid_private_key
        and settings.vapid_subject
        and database_configured()
    )


def save_subscription(subscription: dict[str, Any], preferences: dict | None) -> None:
    endpoint = str(subscription.get("endpoint", ""))
    if not endpoint:
        raise ValueError("Abonnement push invalide.")
    normalized_preferences = {
        key: bool((preferences or {}).get(key, default))
        for key, default in DEFAULT_PREFERENCES.items()
    }
    database().push_subscriptions.update_one(
        {"endpoint": endpoint},
        {
            "$set": {
                "subscription": subscription,
                "preferences": normalized_preferences,
                "updated_at": utc_now(),
            },
            "$setOnInsert": {"created_at": utc_now()},
        },
        upsert=True,
    )


def update_preferences(endpoint: str, preferences: dict[str, Any]) -> None:
    normalized = {
        f"preferences.{key}": bool(preferences.get(key, default))
        for key, default in DEFAULT_PREFERENCES.items()
    }
    normalized["updated_at"] = utc_now()
    database().push_subscriptions.update_one(
        {"endpoint": endpoint},
        {"$set": normalized},
    )


def delete_subscription(endpoint: str) -> None:
    database().push_subscriptions.delete_one({"endpoint": endpoint})


def _send_all_sync(
    *,
    preference: str,
    title: str,
    body: str,
    url: str = "/",
    tag: str = "instagram-studio",
) -> int:
    if not push_configured():
        return 0

    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "url": url,
            "tag": tag,
            "icon": "/static/icons/icon-192.png",
            "badge": "/static/icons/icon-192.png",
        },
        ensure_ascii=False,
    )
    sent = 0
    for document in database().push_subscriptions.find({}):
        preferences = document.get("preferences") or DEFAULT_PREFERENCES
        if not preferences.get(preference, True):
            continue
        try:
            webpush(
                subscription_info=document["subscription"],
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
                timeout=15,
            )
            sent += 1
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {404, 410}:
                database().push_subscriptions.delete_one({"_id": document["_id"]})
    return sent


async def send_notification(**kwargs) -> int:
    return await asyncio.to_thread(_send_all_sync, **kwargs)
