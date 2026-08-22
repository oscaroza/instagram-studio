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
    muted_video_url,
    upload_video,
    upload_video_url,
    verify_cloudinary_connection,
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
from app.services.local_media import (
    LocalMediaError,
    local_media_path,
    mute_local_video,
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
    cloudinary_ready = False
    cloudinary_error = ""
    if cloudinary_configured():
        try:
            cloudinary_ready = await asyncio.to_thread(
                verify_cloudinary_connection
            )
        except CloudinaryMediaError as exc:
            cloudinary_error = str(exc)
    return {
        "ok": True,
        "mongodb_configured": database_configured(),
        "mongodb_ready": mongo_ready,
        "cloudinary_configured": cloudinary_configured(),
        "cloudinary_ready": cloudinary_ready,
        "cloudinary_error": cloudinary_error,
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


@router.post("/library/promote")
async def promote_media_to_library(payload: dict):
    if not database_configured() or not cloudinary_configured():
        return api_error("MongoDB ou Cloudinary n’est pas configuré.", 503)

    video_url = str(payload.get("video_url", "")).strip()
    if not video_url.startswith(("https://", "http://")):
        return api_error("Une URL vidéo publique est nécessaire.")

    cloud_media = None
    try:
        source_path = local_media_path(video_url)
        if source_path is not None:
            cloud_media = await asyncio.to_thread(
                upload_video,
                source_path,
                source_path.name,
            )
        else:
            cloud_media = await asyncio.to_thread(upload_video_url, video_url)

        document = {
            "cloudinary_public_id": cloud_media["public_id"],
            "secure_url": cloud_media["secure_url"],
            "thumbnail_url": cloud_media["thumbnail_url"],
            "bytes": cloud_media["bytes"],
            "duration": cloud_media["duration"],
            "format": cloud_media["format"],
            "width": cloud_media["width"],
            "height": cloud_media["height"],
            "original_filename": cloud_media["original_filename"],
            "created_at": utc_now(),
        }
        result = await asyncio.to_thread(database().media.insert_one, document)
        document["_id"] = result.inserted_id
    except CloudinaryMediaError as exc:
        return api_error(str(exc), 502)
    except Exception:
        if cloud_media and cloud_media.get("public_id"):
            try:
                await asyncio.to_thread(delete_video, cloud_media["public_id"])
            except Exception:
                pass
        return api_error("Impossible d’enregistrer la vidéo programmée.", 503)

    publication_url = cloud_media["secure_url"]
    if bool(payload.get("mute_audio")):
        publication_url = muted_video_url(
            cloud_media["public_id"],
            cloud_media["format"],
        )
    return {
        "ok": True,
        "url": publication_url,
        "media": serialize_document(document),
    }


@router.post("/media/mute")
async def mute_media(payload: dict):
    video_url = str(payload.get("video_url", "")).strip()
    library_id = str(payload.get("library_id", "")).strip()

    if library_id and database_configured() and cloudinary_configured():
        try:
            media = await asyncio.to_thread(
                database().media.find_one,
                {"_id": object_id(library_id)},
            )
            if media:
                return {
                    "ok": True,
                    "url": muted_video_url(
                        media["cloudinary_public_id"],
                        media.get("format", "mp4"),
                    ),
                    "storage": "cloudinary",
                }
        except Exception:
            return api_error("Bibliothèque MongoDB indisponible.", 503)

    try:
        result = await asyncio.to_thread(mute_local_video, video_url)
    except LocalMediaError as exc:
        return api_error(str(exc))
    return {"ok": True, **result, "storage": "temporary"}


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
    library_id = str(payload.get("library_id", "")).strip() or None
    mute_audio = bool(payload.get("mute_audio"))
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

    if mute_audio and library_id and cloudinary_configured():
        try:
            media = await asyncio.to_thread(
                database().media.find_one,
                {"_id": object_id(library_id)},
            )
            if media:
                video_url = muted_video_url(
                    media["cloudinary_public_id"],
                    media.get("format", "mp4"),
                )
        except Exception:
            return api_error("Impossible de préparer la version sans son.", 503)

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
        "library_id": library_id,
        "video_url": video_url,
        "thumbnail_url": str(payload.get("thumbnail_url", "")).strip(),
        "caption": str(payload.get("caption", "")).strip(),
        "hook": str(payload.get("hook", "")).strip(),
        "alt_text": str(payload.get("alt_text", "")).strip(),
        "publication_mode": publication_mode,
        "workflow": workflow,
        "mute_audio": mute_audio,
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
