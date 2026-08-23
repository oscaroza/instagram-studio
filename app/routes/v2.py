import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.analytics import (
    AnalyticsError,
    build_analytics_dashboard,
    save_analytics_report,
    sync_instagram_analytics,
)
from app.services.cerebras import CerebrasError, analyze_instagram_performance
from app.services.cloudinary_media import (
    CloudinaryMediaError,
    cloudinary_configured,
    delete_media,
    muted_video_url,
    upload_image,
    upload_image_url,
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
from app.services.publication_safety import (
    claim_publication,
    publication_checks,
    publication_claim_exists,
    publication_fingerprint,
    release_publication_claim,
)
from app.services.local_media import (
    LocalMediaError,
    local_media_path,
    mute_local_video,
)
from app.services.login_security import (
    list_login_security,
    login_client_context,
    set_device_blocked,
)
from app.services.token_store import token_health


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


def publication_media_items(payload: dict, media_kind: str) -> list[dict[str, str]]:
    raw_items = payload.get("media_items")
    if not isinstance(raw_items, list):
        raw_items = []

    if media_kind == "reel" and not raw_items:
        raw_items = [
            {
                "url": payload.get("video_url", ""),
                "library_id": payload.get("library_id", ""),
                "thumbnail_url": payload.get("thumbnail_url", ""),
                "media_type": "video",
            }
        ]
    elif media_kind == "photo" and not raw_items:
        raw_items = [
            {
                "url": payload.get("image_url") or payload.get("video_url", ""),
                "library_id": payload.get("library_id", ""),
                "thumbnail_url": payload.get("thumbnail_url", ""),
                "media_type": "image",
            }
        ]

    expected_type = "video" if media_kind == "reel" else "image"
    items: list[dict[str, str]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError("Média invalide.")
        url = str(raw_item.get("url", "")).strip()
        if not url.startswith(("https://", "http://")):
            raise ValueError("Chaque média doit avoir une URL publique.")
        media_type = str(raw_item.get("media_type", expected_type)).lower()
        if media_type != expected_type:
            raise ValueError("Le type d’un média ne correspond pas à la publication.")
        items.append(
            {
                "url": url,
                "library_id": str(raw_item.get("library_id", "")).strip(),
                "thumbnail_url": str(raw_item.get("thumbnail_url", "")).strip(),
                "media_type": media_type,
            }
        )

    required = 2 if media_kind == "carousel" else 1
    maximum = 10 if media_kind == "carousel" else 1
    if len(items) < required or len(items) > maximum:
        if media_kind == "carousel":
            raise ValueError("Un carrousel doit contenir entre 2 et 10 photos JPEG.")
        raise ValueError("Ajoute exactement un média à la publication.")
    return items


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


@router.get("/security/login-history")
async def login_history(request: Request):
    try:
        current = login_client_context(request)
        security = await asyncio.to_thread(
            list_login_security,
            current_client_hash=current["client_hash"],
            limit=30,
        )
    except Exception:
        return api_error("Historique des connexions temporairement indisponible.", 503)
    return {"ok": True, **security}


@router.post("/security/devices/{device_key}/block")
async def block_login_device(device_key: str, request: Request):
    current = login_client_context(request)
    if device_key == current["client_hash"]:
        return api_error(
            "Tu ne peux pas bloquer l’appareil que tu utilises actuellement.",
            409,
        )
    try:
        await asyncio.to_thread(set_device_blocked, device_key, True)
    except ValueError as exc:
        return api_error(str(exc), 404)
    return {"ok": True}


@router.post("/security/devices/{device_key}/unblock")
async def unblock_login_device(device_key: str):
    try:
        await asyncio.to_thread(set_device_blocked, device_key, False)
    except ValueError as exc:
        return api_error(str(exc), 404)
    return {"ok": True}


@router.get("/instagram/token-health")
async def instagram_token_health():
    try:
        health = await asyncio.to_thread(token_health)
    except Exception:
        return api_error("État du token Instagram temporairement indisponible.", 503)
    return {"ok": True, **health}


@router.get("/analytics/dashboard")
async def analytics_dashboard():
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)
    try:
        dashboard = await asyncio.to_thread(build_analytics_dashboard)
    except Exception:
        return api_error("Dashboard MongoDB indisponible.", 503)
    return {"ok": True, **dashboard}


@router.post("/analytics/sync")
async def synchronize_analytics():
    try:
        result = await sync_instagram_analytics()
    except AnalyticsError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Synchronisation Instagram temporairement indisponible.", 503)
    return {"ok": True, "sync": result}


@router.post("/analytics/assistant")
async def run_analytics_assistant():
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)
    try:
        dashboard = await asyncio.to_thread(build_analytics_dashboard)
        summary = dashboard.get("summary") or {}
        if int(summary.get("media_count", 0)) < 3:
            return api_error(
                "Synchronise au moins 3 publications avant de lancer l’assistant.",
                409,
            )
        if not any(int(summary.get(name, 0)) for name in ("views", "reach", "interactions")):
            return api_error(
                "Les publications synchronisées ne contiennent pas encore assez de statistiques.",
                409,
            )
        report = await analyze_instagram_performance(dashboard)
        await asyncio.to_thread(
            save_analytics_report,
            report,
            (dashboard.get("sync") or {}).get("last_synced_at"),
            settings.cerebras_model,
        )
    except CerebrasError as exc:
        return api_error(str(exc), 502)
    except AnalyticsError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Assistant Groq temporairement indisponible.", 503)
    return {"ok": True, "report": report, "model": settings.cerebras_model}


@router.post("/publications/preflight")
async def publication_preflight(payload: dict):
    media_kind = str(payload.get("media_kind", "reel")).strip().lower()
    if media_kind not in {"reel", "photo", "carousel"}:
        return api_error("Type de publication invalide.")
    try:
        media_items = publication_media_items(payload, media_kind)
        caption = str(payload.get("caption", "")).strip()
        publication_mode = str(payload.get("publication_mode", "normal")).lower()
        workflow = str(payload.get("workflow", "auto_publish")).lower()
        scheduled_for = str(payload.get("scheduled_for", "")).strip()
        if scheduled_for:
            parsed = parse_datetime(scheduled_for)
            if parsed <= utc_now() + timedelta(seconds=30):
                raise ValueError("Programme la publication au moins une minute à l’avance.")
        checks = publication_checks(
            media_kind=media_kind,
            media_items=media_items,
            caption=caption,
            publication_mode=publication_mode,
            workflow=workflow,
            scheduled_for=scheduled_for,
        )
        key = publication_fingerprint(
            media_kind=media_kind,
            media_items=media_items,
            caption=caption,
            publication_mode=publication_mode,
            workflow=workflow,
            scheduled_for=scheduled_for,
        )
        if await asyncio.to_thread(publication_claim_exists, key):
            return api_error(
                "Cette publication identique est déjà en cours ou vient d’être envoyée.",
                409,
            )
    except ValueError as exc:
        return api_error(str(exc))
    return {"ok": True, "checks": checks}


@router.get("/library")
async def list_library():
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)

    def query():
        documents = list(database().media.find({}).sort("created_at", -1).limit(200))
        total_bytes = sum(int(item.get("bytes", 0)) for item in documents)
        media_ids = [str(item["_id"]) for item in documents]
        usage: dict[str, dict[str, Any]] = {
            media_id: {"usage_count": 0, "active_usage_count": 0, "last_used_at": None}
            for media_id in media_ids
        }
        if media_ids:
            publications = database().publications.find(
                {
                    "$or": [
                        {"library_id": {"$in": media_ids}},
                        {"library_ids": {"$in": media_ids}},
                    ]
                },
                {
                    "library_id": 1,
                    "library_ids": 1,
                    "status": 1,
                    "scheduled_for": 1,
                    "published_at": 1,
                    "created_at": 1,
                },
            )
            for publication in publications:
                references = set(publication.get("library_ids") or [])
                if publication.get("library_id"):
                    references.add(publication["library_id"])
                used_at = (
                    publication.get("published_at")
                    or publication.get("scheduled_for")
                    or publication.get("created_at")
                )
                for media_id in references:
                    if media_id not in usage:
                        continue
                    usage[media_id]["usage_count"] += 1
                    if publication.get("status") in {
                        "scheduled",
                        "publishing",
                        "awaiting_manual",
                    }:
                        usage[media_id]["active_usage_count"] += 1
                    previous = usage[media_id]["last_used_at"]
                    if used_at and (not previous or used_at > previous):
                        usage[media_id]["last_used_at"] = used_at
        for item in documents:
            item.update(usage[str(item["_id"])])
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

    media_type = str(payload.get("media_type", "video")).strip().lower()
    if media_type not in {"image", "video"}:
        return api_error("Type de média invalide.")
    media_url = str(
        payload.get("media_url")
        or payload.get("image_url")
        or payload.get("video_url")
        or ""
    ).strip()
    if not media_url.startswith(("https://", "http://")):
        return api_error("Une URL publique est nécessaire.")

    cloud_media = None
    try:
        source_path = local_media_path(media_url)
        if source_path is not None:
            upload_function = upload_image if media_type == "image" else upload_video
            cloud_media = await asyncio.to_thread(
                upload_function, source_path, source_path.name
            )
        else:
            upload_function = (
                upload_image_url if media_type == "image" else upload_video_url
            )
            cloud_media = await asyncio.to_thread(upload_function, media_url)

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
            "description": str(payload.get("description", "")).strip()[:500],
            "media_type": cloud_media["media_type"],
            "resource_type": cloud_media["resource_type"],
            "created_at": utc_now(),
        }
        result = await asyncio.to_thread(database().media.insert_one, document)
        document["_id"] = result.inserted_id
    except CloudinaryMediaError as exc:
        return api_error(str(exc), 502)
    except Exception:
        if cloud_media and cloud_media.get("public_id"):
            try:
                await asyncio.to_thread(
                    delete_media,
                    cloud_media["public_id"],
                    cloud_media.get("resource_type", media_type),
                )
            except Exception:
                pass
        return api_error("Impossible d’enregistrer le média programmé.", 503)

    publication_url = cloud_media["secure_url"]
    if media_type == "video" and bool(payload.get("mute_audio")):
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
                "$or": [
                    {"library_id": media_id},
                    {"library_ids": media_id},
                ],
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
        await asyncio.to_thread(
            delete_media,
            media["cloudinary_public_id"],
            media.get("resource_type", media.get("media_type", "video")),
        )
        await asyncio.to_thread(database().media.delete_one, {"_id": identifier})
    except CloudinaryMediaError as exc:
        return api_error(str(exc), 502)
    return {"ok": True}


@router.post("/publications")
async def create_publication(payload: dict):
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)

    media_kind = str(payload.get("media_kind", "reel")).strip().lower()
    if media_kind not in {"reel", "photo", "carousel"}:
        return api_error("Type de publication invalide.")
    try:
        media_items = publication_media_items(payload, media_kind)
    except ValueError as exc:
        return api_error(str(exc))

    library_ids = [item["library_id"] for item in media_items if item["library_id"]]
    library_id = library_ids[0] if len(library_ids) == 1 else None
    mute_audio = bool(payload.get("mute_audio")) if media_kind == "reel" else False

    publication_mode = str(payload.get("publication_mode", "normal")).lower()
    if publication_mode not in {"normal", "trial"}:
        return api_error("Mode Reel invalide.")
    if media_kind != "reel" and publication_mode != "normal":
        return api_error("Le mode Trial est réservé aux Reels.")
    if publication_mode == "trial" and not settings.enable_trial_reels:
        return api_error("Les Trial Reels sont désactivés.", 409)

    workflow = str(payload.get("workflow", "auto_publish")).lower()
    if workflow not in {"auto_publish", "manual_music"}:
        return api_error("Workflow de publication invalide.")

    caption = str(payload.get("caption", "")).strip()
    scheduled_text = str(payload.get("scheduled_for", "")).strip()
    try:
        publication_checks(
            media_kind=media_kind,
            media_items=media_items,
            caption=caption,
            publication_mode=publication_mode,
            workflow=workflow,
            scheduled_for=scheduled_text,
        )
    except ValueError as exc:
        return api_error(str(exc))
    dedupe_key = publication_fingerprint(
        media_kind=media_kind,
        media_items=media_items,
        caption=caption,
        publication_mode=publication_mode,
        workflow=workflow,
        scheduled_for=scheduled_text,
    )
    if not await asyncio.to_thread(claim_publication, dedupe_key):
        return api_error(
            "Cette publication identique est déjà en cours ou vient d’être envoyée.",
            409,
        )

    if mute_audio and library_id and cloudinary_configured():
        try:
            media = await asyncio.to_thread(
                database().media.find_one,
                {"_id": object_id(library_id)},
            )
            if media:
                media_items[0]["url"] = muted_video_url(
                    media["cloudinary_public_id"],
                    media.get("format", "mp4"),
                )
        except Exception:
            await asyncio.to_thread(release_publication_claim, dedupe_key)
            return api_error("Impossible de préparer la version sans son.", 503)

    scheduled_value = payload.get("scheduled_for")
    if scheduled_value:
        if len(library_ids) != len(media_items):
            await asyncio.to_thread(release_publication_claim, dedupe_key)
            return api_error(
                "Chaque média programmé doit d’abord être enregistré dans Cloudinary."
            )
        try:
            scheduled_for = parse_datetime(scheduled_value)
        except ValueError as exc:
            await asyncio.to_thread(release_publication_claim, dedupe_key)
            return api_error(str(exc))
        if scheduled_for <= utc_now() + timedelta(seconds=30):
            await asyncio.to_thread(release_publication_claim, dedupe_key)
            return api_error("Programme la publication au moins une minute à l’avance.")
        status = "scheduled"
    elif workflow == "manual_music":
        scheduled_for = None
        status = "awaiting_manual"
    else:
        await asyncio.to_thread(release_publication_claim, dedupe_key)
        return api_error("Une date est requise pour programmer cette publication.")

    document = {
        "title": str(payload.get("title", "Publication Instagram")).strip()[:120]
        or "Publication Instagram",
        "media_kind": media_kind,
        "media_items": media_items,
        "library_ids": library_ids,
        "library_id": library_id,
        "video_url": media_items[0]["url"] if media_kind == "reel" else "",
        "image_url": media_items[0]["url"] if media_kind == "photo" else "",
        "thumbnail_url": media_items[0].get("thumbnail_url", ""),
        "caption": caption,
        "hook": str(payload.get("hook", "")).strip(),
        "alt_text": str(payload.get("alt_text", "")).strip(),
        "publication_mode": publication_mode,
        "workflow": workflow,
        "mute_audio": mute_audio,
        "status": status,
        "scheduled_for": scheduled_for,
        "timezone": str(payload.get("timezone", "Europe/Paris")),
        "dedupe_key": dedupe_key,
        "attempts": 0,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }

    try:
        result = await asyncio.to_thread(database().publications.insert_one, document)
    except Exception:
        await asyncio.to_thread(release_publication_claim, dedupe_key)
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


@router.patch("/publications/{publication_id}/schedule")
async def reschedule_publication(publication_id: str, payload: dict):
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)
    try:
        identifier = object_id(publication_id)
        scheduled_for = parse_datetime(payload.get("scheduled_for"))
    except ValueError as exc:
        return api_error(str(exc))
    if scheduled_for <= utc_now() + timedelta(seconds=30):
        return api_error("Déplace la publication au moins une minute dans le futur.")

    result = await asyncio.to_thread(
        database().publications.update_one,
        {"_id": identifier, "status": "scheduled"},
        {
            "$set": {
                "scheduled_for": scheduled_for,
                "updated_at": utc_now(),
            },
            "$unset": {
                "reminder_sent_at": "",
            },
        },
    )
    if not result.modified_count:
        return api_error(
            "Cette publication n’est plus programmable ou la date n’a pas changé.",
            409,
        )
    return {"ok": True, "scheduled_for": scheduled_for.isoformat()}


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
