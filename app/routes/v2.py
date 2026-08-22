import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.cloudinary_media import (
    CloudinaryMediaError,
    cloudinary_configured,
    delete_video,
)
from app.services.database import (
    DatabaseUnavailable,
    database,
    database_configured,
    object_id,
    serialize_document,
    utc_now,
)
from app.services.push_notifications import (
    DEFAULT_PREFERENCES,
    delete_subscription,
    push_configured,
    save_subscription,
    update_preferences,
)


router = APIRouter(prefix="/api")


def api_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": message},
        status_code=status_code,
    )


def parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Date ou heure invalide.") from exc
    if parsed.tzinfo is None:
        raise ValueError("Le fuseau horaire est obligatoire.")
    return parsed.astimezone(timezone.utc)


@router.get("/v2/status")
async def v2_status():
    mongo_ready = False
    if database_configured():
        try:
            mongo_ready = await asyncio.to_thread(
                lambda: bool(database().command("ping").get("ok"))
            )
        except Exception:
            mongo_ready = False
    return {
        "ok": True,
        "mongodb_configured": database_configured(),
        "mongodb_ready": mongo_ready,
        "cloudinary_ready": cloudinary_configured(),
        "push_ready": push_configured(),
        "trial_reels_enabled": settings.enable_trial_reels,
    }


@router.get("/library")
async def list_library():
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)

    def query():
        documents = list(database().media.find({}).sort("created_at", -1).limit(200))
        total_bytes = sum(int(item.get("bytes", 0)) for item in documents)
        return documents, total_bytes

    try:
        documents, total_bytes = await asyncio.to_thread(query)
    except Exception:
        return api_error("Bibliothèque MongoDB indisponible.", 503)

    return {
        "ok": True,
        "items": [serialize_document(item) for item in documents],
        "total_bytes": total_bytes,
    }


@router.delete("/library/{media_id}")
async def remove_library_media(media_id: str):
    if not database_configured() or not cloudinary_configured():
        return api_error("MongoDB ou Cloudinary n’est pas configuré.", 503)
    try:
        identifier = object_id(media_id)
    except ValueError as exc:
        return api_error(str(exc))

    def load():
        media = database().media.find_one({"_id": identifier})
        active = database().publications.find_one(
            {
                "library_id": media_id,
                "status": {"$in": ["scheduled", "publishing", "awaiting_manual"]},
            }
        )
        return media, active

    media, active = await asyncio.to_thread(load)
    if not media:
        return api_error("Média introuvable.", 404)
    if active:
        return api_error(
            "Ce média est utilisé par une publication programmée. Annule-la d’abord.",
            409,
        )

    try:
        await asyncio.to_thread(delete_video, media["cloudinary_public_id"])
        await asyncio.to_thread(database().media.delete_one, {"_id": identifier})
    except CloudinaryMediaError as exc:
        return api_error(str(exc), 502)
    return {"ok": True}


@router.post("/publications")
async def create_publication(payload: dict):
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)

    video_url = str(payload.get("video_url", "")).strip()
    if not video_url.startswith(("https://", "http://")):
        return api_error("Une vidéo publique Cloudinary est nécessaire.")

    publication_mode = str(payload.get("publication_mode", "normal")).lower()
    if publication_mode not in {"normal", "trial"}:
        return api_error("Mode Reel invalide.")
    if publication_mode == "trial" and not settings.enable_trial_reels:
        return api_error("Les Trial Reels sont désactivés.", 409)

    workflow = str(payload.get("workflow", "auto_publish")).lower()
    if workflow not in {"auto_publish", "manual_music"}:
        return api_error("Workflow de publication invalide.")

    scheduled_value = payload.get("scheduled_for")
    if scheduled_value:
        try:
            scheduled_for = parse_datetime(scheduled_value)
        except ValueError as exc:
            return api_error(str(exc))
        if scheduled_for <= utc_now() + timedelta(seconds=30):
            return api_error("Programme la publication au moins une minute à l’avance.")
        status = "scheduled"
    elif workflow == "manual_music":
        scheduled_for = None
        status = "awaiting_manual"
    else:
        return api_error("Une date est requise pour programmer cette publication.")

    document = {
        "title": str(payload.get("title", "Publication Instagram")).strip()[:120]
        or "Publication Instagram",
        "library_id": str(payload.get("library_id", "")).strip() or None,
        "video_url": video_url,
        "thumbnail_url": str(payload.get("thumbnail_url", "")).strip(),
        "caption": str(payload.get("caption", "")).strip(),
        "hook": str(payload.get("hook", "")).strip(),
        "alt_text": str(payload.get("alt_text", "")).strip(),
        "publication_mode": publication_mode,
        "workflow": workflow,
        "status": status,
        "scheduled_for": scheduled_for,
        "timezone": str(payload.get("timezone", "Europe/Paris")),
        "attempts": 0,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }

    try:
        result = await asyncio.to_thread(database().publications.insert_one, document)
    except Exception:
        return api_error("Impossible d’enregistrer la publication dans MongoDB.", 503)

    document["_id"] = result.inserted_id
    return {"ok": True, "publication": serialize_document(document)}


@router.get("/publications/calendar")
async def calendar_publications(start: str = "", end: str = ""):
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)
    now = utc_now()
    try:
        start_date = parse_datetime(start) if start else now - timedelta(days=45)
        end_date = parse_datetime(end) if end else now + timedelta(days=90)
    except ValueError as exc:
        return api_error(str(exc))

    query = {
        "$or": [
            {"scheduled_for": {"$gte": start_date, "$lt": end_date}},
            {"published_at": {"$gte": start_date, "$lt": end_date}},
            {
                "status": {"$in": ["awaiting_manual", "failed"]},
                "created_at": {"$gte": start_date, "$lt": end_date},
            },
        ]
    }
    documents = await asyncio.to_thread(
        lambda: list(database().publications.find(query).sort("scheduled_for", 1).limit(500))
    )
    return {
        "ok": True,
        "items": [serialize_document(item) for item in documents],
    }


@router.delete("/publications/{publication_id}")
async def cancel_publication(publication_id: str):
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)
    try:
        identifier = object_id(publication_id)
    except ValueError as exc:
        return api_error(str(exc))
    result = await asyncio.to_thread(
        database().publications.update_one,
        {"_id": identifier, "status": {"$in": ["scheduled", "failed", "awaiting_manual"]}},
        {"$set": {"status": "cancelled", "updated_at": utc_now()}},
    )
    if not result.modified_count:
        return api_error("Cette publication ne peut plus être annulée.", 409)
    return {"ok": True}


@router.get("/push/config")
async def push_configuration():
    return {
        "ok": True,
        "configured": push_configured(),
        "public_key": settings.vapid_public_key if push_configured() else "",
        "default_preferences": DEFAULT_PREFERENCES,
    }


@router.post("/push/subscriptions")
async def subscribe_push(payload: dict):
    if not push_configured():
        return api_error("Les notifications Push ne sont pas configurées.", 503)
    try:
        await asyncio.to_thread(
            save_subscription,
            payload.get("subscription") or {},
            payload.get("preferences") or {},
        )
    except (ValueError, DatabaseUnavailable) as exc:
        return api_error(str(exc))
    return {"ok": True}


@router.patch("/push/subscriptions")
async def change_push_preferences(payload: dict):
    if not push_configured():
        return api_error("Les notifications Push ne sont pas configurées.", 503)
    endpoint = str(payload.get("endpoint", ""))
    if not endpoint:
        return api_error("Endpoint Push manquant.")
    await asyncio.to_thread(
        update_preferences,
        endpoint,
        payload.get("preferences") or {},
    )
    return {"ok": True}


@router.delete("/push/subscriptions")
async def unsubscribe_push(payload: dict):
    endpoint = str(payload.get("endpoint", ""))
    if endpoint and database_configured():
        await asyncio.to_thread(delete_subscription, endpoint)
    return {"ok": True}
