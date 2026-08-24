import hashlib
import json
import threading
from datetime import timedelta
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.services.database import database, database_configured, utc_now


DEDUPLICATION_MINUTES = 15
_local_lock = threading.Lock()
_local_claims: dict[str, Any] = {}


def publication_fingerprint(
    *,
    media_kind: str,
    media_items: list[dict[str, Any]],
    caption: str,
    publication_mode: str,
    workflow: str,
    scheduled_for: str = "",
) -> str:
    canonical = {
        "media_kind": media_kind,
        "media_urls": [str(item.get("url", "")).strip() for item in media_items],
        "caption": caption.strip(),
        "publication_mode": publication_mode,
        "workflow": workflow,
        "scheduled_for": str(scheduled_for or "").strip(),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def publication_checks(
    *,
    media_kind: str,
    media_items: list[dict[str, Any]],
    caption: str,
    publication_mode: str,
    workflow: str,
    scheduled_for: str = "",
) -> list[str]:
    if len(caption) > 2200:
        raise ValueError(
            f"La légende contient {len(caption)} caractères. Limite : 2 200."
        )
    if publication_mode == "trial" and media_kind != "reel":
        raise ValueError("Le mode Trial est réservé aux Reels.")
    if workflow not in {"auto_publish", "manual_music"}:
        raise ValueError("Workflow de publication invalide.")
    if media_kind == "story" and workflow != "auto_publish":
        raise ValueError(
            "La musique et les stickers de Story ne sont pas disponibles via l’API Meta."
        )

    type_label = {
        "reel": "Reel",
        "photo": "Photo",
        "carousel": "Carrousel",
        "story": "Story",
    }[media_kind]
    checks = [f"{type_label} • {len(media_items)} média(s) valide(s)"]
    if media_kind == "story":
        checks.append(
            "Texte conservé comme note dans le Studio — Meta ne publie pas de légende sur une Story"
        )
    else:
        checks.append(
            f"Légende prête • {len(caption)} / 2 200 caractères"
            if caption
            else "Publication sans légende"
        )
    checks.append(
        "Finalisation manuelle dans Instagram pour la musique"
        if workflow == "manual_music"
        else "Publication automatique via l’API Instagram"
    )
    checks.append(
        "Date de programmation valide"
        if scheduled_for
        else "Publication immédiate"
    )
    return checks


def _clear_expired_local_claims() -> None:
    now = utc_now()
    expired = [key for key, expires_at in _local_claims.items() if expires_at <= now]
    for key in expired:
        _local_claims.pop(key, None)


def publication_claim_exists(key: str) -> bool:
    now = utc_now()
    if database_configured():
        try:
            return (
                database().publication_claims.count_documents(
                    {"_id": key, "expires_at": {"$gt": now}}, limit=1
                )
                > 0
            )
        except Exception:
            pass
    with _local_lock:
        _clear_expired_local_claims()
        return key in _local_claims


def claim_publication(key: str) -> bool:
    now = utc_now()
    expires_at = now + timedelta(minutes=DEDUPLICATION_MINUTES)
    if database_configured():
        try:
            database().publication_claims.insert_one(
                {"_id": key, "created_at": now, "expires_at": expires_at}
            )
            return True
        except DuplicateKeyError:
            # MongoDB nettoie les index TTL de façon différée. Une empreinte
            # réellement expirée peut donc encore exister quelques secondes.
            try:
                result = database().publication_claims.update_one(
                    {"_id": key, "expires_at": {"$lte": now}},
                    {"$set": {"created_at": now, "expires_at": expires_at}},
                )
                return bool(result.modified_count)
            except Exception:
                return False
        except Exception:
            pass
    with _local_lock:
        _clear_expired_local_claims()
        if key in _local_claims:
            return False
        _local_claims[key] = expires_at
        return True


def release_publication_claim(key: str) -> None:
    if database_configured():
        try:
            database().publication_claims.delete_one({"_id": key})
        except Exception:
            pass
    with _local_lock:
        _local_claims.pop(key, None)
