import json
import uuid
from typing import Any

from app.services.database import database, object_id, serialize_document, utc_now


MAX_DRAFTS = 100
ALLOWED_MEDIA_KINDS = {
    "reel",
    "photo",
    "carousel",
    "carousel_video",
    "story_photo",
    "story_video",
}
TEXT_LIMITS = {
    "videoUrl": 2048,
    "libraryId": 120,
    "thumbnailUrl": 2048,
    "description": 3000,
    "location": 240,
    "drone": 240,
    "language": 80,
    "tone": 120,
    "extra": 2000,
    "calendarEntryTitle": 160,
    "caption": 5000,
    "hashtags": 3000,
    "altText": 1500,
    "hook": 1200,
    "scheduledFor": 80,
}


def _text(payload: dict[str, Any], key: str, limit: int) -> str:
    return str(payload.get(key, ""))[:limit]


def _editor_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("layers"), list):
        return None
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 30_000:
        raise ValueError("L’éditeur de texte du brouillon est trop volumineux.")
    return value


def _media_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = payload.get("media_items")
    if raw_items is None:
        try:
            raw_items = json.loads(str(payload.get("mediaItemsJson") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("La liste des médias du brouillon est invalide.") from exc
    if not isinstance(raw_items, list) or len(raw_items) > 10:
        raise ValueError("Un brouillon peut contenir au maximum 10 médias.")

    items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError("Un média du brouillon est invalide.")
        url = str(raw_item.get("url", "")).strip()
        if not url.startswith(("https://", "http://")):
            raise ValueError("Chaque média du brouillon doit avoir une URL publique.")
        media_type = str(raw_item.get("media_type", "image")).lower()
        if media_type not in {"image", "video"}:
            raise ValueError("Type de média invalide dans le brouillon.")
        item: dict[str, Any] = {
            "url": url[:2048],
            "library_id": str(raw_item.get("library_id", ""))[:120],
            "thumbnail_url": str(raw_item.get("thumbnail_url", ""))[:2048],
            "media_type": media_type,
            "name": str(raw_item.get("name", ""))[:240],
        }
        for key in ("size", "source_size"):
            try:
                item[key] = max(0, int(raw_item.get(key) or 0))
            except (TypeError, ValueError):
                item[key] = 0
        item["optimized"] = bool(raw_item.get("optimized"))
        editor_state = _editor_state(raw_item.get("text_editor"))
        if media_type == "image" and editor_state:
            item["text_editor"] = editor_state
            original_url = str(raw_item.get("original_url", "")).strip()
            if original_url.startswith(("https://", "http://")):
                item["original_url"] = original_url[:2048]
            item["original_library_id"] = str(
                raw_item.get("original_library_id", "")
            )[:120]
        items.append(item)
    return items


def normalize_draft(payload: dict[str, Any]) -> dict[str, Any]:
    media_kind = str(payload.get("mediaKind", "reel"))
    if media_kind not in ALLOWED_MEDIA_KINDS:
        media_kind = "reel"
    publication_mode = str(payload.get("publicationMode", "normal"))
    if publication_mode not in {"normal", "trial"}:
        publication_mode = "normal"
    client_id = str(payload.get("clientId") or payload.get("id") or uuid.uuid4())[:80]

    draft: dict[str, Any] = {
        "client_id": client_id,
        "mediaKind": media_kind,
        "publicationMode": publication_mode,
        "media_items": _media_items(payload),
    }
    for key, limit in TEXT_LIMITS.items():
        draft[key] = _text(payload, key, limit)
    for key in ("scheduleEnabled", "musicEnabled", "muteAudio"):
        draft[key] = bool(payload.get(key))
    draft["imageOptimizationEnabled"] = bool(
        payload.get("imageOptimizationEnabled", True)
    )
    return draft


def draft_for_client(document: dict[str, Any]) -> dict[str, Any]:
    result = serialize_document(document)
    result["clientId"] = result.pop("client_id", "")
    result["mediaItemsJson"] = json.dumps(
        result.pop("media_items", []), ensure_ascii=False, separators=(",", ":")
    )
    result["savedAt"] = result.get("updated_at") or result.get("created_at")
    return result


def list_drafts() -> list[dict[str, Any]]:
    documents = list(database().drafts.find({}).sort("updated_at", -1).limit(MAX_DRAFTS))
    return [draft_for_client(document) for document in documents]


def save_draft(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_draft(payload)
    now = utc_now()
    collection = database().drafts
    collection.update_one(
        {"client_id": normalized["client_id"]},
        {
            "$set": {**normalized, "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    document = collection.find_one({"client_id": normalized["client_id"]})
    if not document:
        raise RuntimeError("Le brouillon n’a pas pu être relu après son enregistrement.")

    stale_ids = [
        item["_id"]
        for item in collection.find({}, {"_id": 1})
        .sort("updated_at", -1)
        .skip(MAX_DRAFTS)
    ]
    if stale_ids:
        collection.delete_many({"_id": {"$in": stale_ids}})
    return draft_for_client(document)


def delete_draft(draft_id: str) -> bool:
    result = database().drafts.delete_one({"_id": object_id(draft_id)})
    return bool(result.deleted_count)


def delete_all_drafts() -> int:
    return int(database().drafts.delete_many({}).deleted_count)
