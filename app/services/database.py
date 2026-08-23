from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import certifi
from bson import ObjectId
from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

from app.config import settings


class DatabaseUnavailable(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def database_configured() -> bool:
    return bool(settings.mongodb_uri)


@lru_cache(maxsize=1)
def mongo_client() -> MongoClient:
    if not database_configured():
        raise DatabaseUnavailable("MONGODB_URI n’est pas configurée.")
    return MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        tls=True,
        tlsCAFile=certifi.where(),
        appname="instagram-studio",
        tz_aware=True,
    )


def database() -> Database:
    return mongo_client()[settings.mongodb_database]


def ping_database() -> bool:
    if not database_configured():
        return False
    mongo_client().admin.command("ping")
    return True


def ensure_indexes() -> None:
    db = database()
    db.media.create_index([("created_at", ASCENDING)])
    db.publications.create_index([("status", ASCENDING), ("scheduled_for", ASCENDING)])
    db.publications.create_index([("library_id", ASCENDING)])
    db.publications.create_index([("library_ids", ASCENDING)])
    db.push_subscriptions.create_index("endpoint", unique=True)
    db.instagram_credentials.create_index("updated_at")
    db.instagram_media.create_index([("timestamp", ASCENDING)])
    db.instagram_insight_snapshots.create_index(
        [("media_id", ASCENDING), ("captured_at", ASCENDING)]
    )
    db.analytics_reports.create_index("created_at")
    db.publication_claims.create_index("expires_at", expireAfterSeconds=0)
    db.login_rate_limits.create_index("updated_at", expireAfterSeconds=86400)
    db.login_events.create_index("created_at", expireAfterSeconds=7776000)
    db.blocked_devices.create_index("updated_at")
    db.passkeys.create_index("created_at")
    db.passkey_challenges.create_index("expires_at", expireAfterSeconds=0)
    db.analytics_assistant_messages.create_index("created_at")
    db.analytics_assistant_messages.create_index("expires_at", expireAfterSeconds=0)


def object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception as exc:
        raise ValueError("Identifiant MongoDB invalide.") from exc


def serialize_document(document: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in document.items():
        public_key = "id" if key == "_id" else key
        if isinstance(value, ObjectId):
            result[public_key] = str(value)
        elif isinstance(value, datetime):
            result[public_key] = value.astimezone(timezone.utc).isoformat()
        else:
            result[public_key] = value
    return result
