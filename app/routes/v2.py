import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.services.analytics import (
    AnalyticsError,
    build_analytics_dashboard,
    clear_assistant_messages,
    get_analytics_sync_progress,
    list_assistant_messages,
    save_assistant_exchange,
    save_analytics_report,
    save_content_ideas,
    sync_instagram_analytics,
)
from app.services.autopilot import (
    AutopilotError,
    analyze_autopilot_media,
    generate_autopilot_plan,
)
from app.services.cerebras import (
    CerebrasError,
    analyze_instagram_performance,
    chat_instagram_performance,
    generate_growth_content_ideas,
)
from app.services.cloudflare_usage import r2_usage_summary
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
    send_notification,
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
from app.services.media_storage import (
    MediaStorageError,
    active_storage_provider,
    delete_stored_media,
    media_storage_configured,
    prepare_muted_media,
    storage_provider_label,
    store_media_path,
    store_media_url,
    stored_media_provider,
    verify_active_storage,
)
from app.services.login_security import (
    list_login_security,
    login_attempt_status,
    login_client_context,
    record_login_success,
    set_device_blocked,
)
from app.services.passkeys import (
    PasskeyError,
    authentication_options,
    delete_passkey,
    list_passkeys,
    registration_options,
    verify_authentication,
    verify_registration,
)
from app.services.preferences import (
    get_appearance_preferences,
    reset_appearance_preferences,
    save_appearance_preferences,
)
from app.security import (
    SESSION_COOKIE,
    create_session_token,
    request_is_authenticated,
    safe_next_path,
    session_max_age_seconds,
)
from app.services.token_store import token_health
from app.services.realtime import publish_calendar_change, realtime_event_stream


router = APIRouter(prefix="/api")


def api_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": message},
        status_code=status_code,
    )


@router.get("/events")
async def studio_events(request: Request):
    return StreamingResponse(
        realtime_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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
    elif media_kind == "story" and not raw_items:
        story_media_type = str(payload.get("story_media_type", "image")).lower()
        raw_items = [
            {
                "url": payload.get("video_url") or payload.get("image_url", ""),
                "library_id": payload.get("library_id", ""),
                "thumbnail_url": payload.get("thumbnail_url", ""),
                "media_type": story_media_type,
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
        allowed_types = (
            {"image", "video"}
            if media_kind in {"carousel", "story"}
            else {expected_type}
        )
        if media_type not in allowed_types:
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
            raise ValueError("Un carrousel doit contenir entre 2 et 10 médias.")
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
    storage_ready = False
    storage_error = ""
    storage_usage_bytes = 0
    storage_limit_bytes = 0
    if media_storage_configured():
        try:
            storage_status = await asyncio.to_thread(verify_active_storage)
            storage_ready = bool(storage_status.get("ready"))
            storage_usage_bytes = int(storage_status.get("usage_bytes", 0))
            storage_limit_bytes = int(storage_status.get("limit_bytes", 0))
        except MediaStorageError as exc:
            storage_error = str(exc)
    provider = active_storage_provider()
    return {
        "ok": True,
        "mongodb_configured": database_configured(),
        "mongodb_ready": mongo_ready,
        "media_storage_provider": provider,
        "media_storage_label": storage_provider_label(provider),
        "media_storage_configured": media_storage_configured(),
        "media_storage_ready": storage_ready,
        "media_storage_error": storage_error,
        "media_storage_usage_bytes": storage_usage_bytes,
        "media_storage_limit_bytes": storage_limit_bytes,
        "push_ready": push_configured(),
        "trial_reels_enabled": settings.enable_trial_reels,
        "stories_enabled": settings.enable_instagram_stories,
    }


@router.get("/r2/usage")
async def r2_usage(request: Request):
    if not request_is_authenticated(request):
        return api_error("Session requise pour charger les statistiques Cloudflare.", 401)
    if active_storage_provider() != "r2":
        return api_error("Cloudflare R2 n’est pas le stockage média actif.", 409)
    try:
        usage = await asyncio.to_thread(r2_usage_summary)
    except Exception:
        return api_error("Statistiques Cloudflare temporairement indisponibles.", 503)
    return {"ok": True, "usage": usage}


@router.get("/preferences/appearance")
async def get_appearance(request: Request):
    if not request_is_authenticated(request):
        return api_error("Session requise pour charger la personnalisation.", 401)
    try:
        appearance = await asyncio.to_thread(get_appearance_preferences)
    except Exception:
        return api_error("Personnalisation temporairement indisponible.", 503)
    return {"ok": True, "appearance": appearance}


@router.put("/preferences/appearance")
async def update_appearance(request: Request, payload: dict):
    if not request_is_authenticated(request):
        return api_error("Session requise pour modifier la personnalisation.", 401)
    try:
        appearance = await asyncio.to_thread(save_appearance_preferences, payload)
    except ValueError as exc:
        return api_error(str(exc))
    except RuntimeError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Enregistrement de la personnalisation impossible.", 503)
    return {"ok": True, "appearance": appearance}


@router.delete("/preferences/appearance")
async def delete_appearance(request: Request):
    if not request_is_authenticated(request):
        return api_error("Session requise pour réinitialiser la personnalisation.", 401)
    try:
        appearance = await asyncio.to_thread(reset_appearance_preferences)
    except RuntimeError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Réinitialisation de la personnalisation impossible.", 503)
    return {"ok": True, "appearance": appearance}


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


@router.get("/passkeys")
async def get_passkeys(request: Request):
    if not request_is_authenticated(request):
        return api_error("Session requise pour gérer les passkeys.", 401)
    try:
        items = await asyncio.to_thread(list_passkeys)
    except PasskeyError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Passkeys temporairement indisponibles.", 503)
    return {"ok": True, "items": items}


@router.post("/passkeys/register/options")
async def begin_passkey_registration(request: Request):
    if not request_is_authenticated(request):
        return api_error("Session requise pour ajouter une passkey.", 401)
    context = login_client_context(request)
    try:
        result = await asyncio.to_thread(registration_options, context["client_hash"])
    except PasskeyError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Création de la passkey temporairement indisponible.", 503)
    return {"ok": True, **result}


@router.post("/passkeys/register/verify")
async def finish_passkey_registration(request: Request, payload: dict):
    if not request_is_authenticated(request):
        return api_error("Session requise pour ajouter une passkey.", 401)
    context = login_client_context(request)
    credential = payload.get("credential")
    if not isinstance(credential, dict):
        return api_error("Réponse passkey invalide.")
    try:
        item = await asyncio.to_thread(
            verify_registration,
            str(payload.get("ceremony_id") or ""),
            credential,
            context["client_hash"],
            str(payload.get("label") or "Face ID / passkey"),
        )
    except PasskeyError as exc:
        return api_error(str(exc), 400)
    except Exception:
        return api_error("Enregistrement de la passkey temporairement indisponible.", 503)
    return {"ok": True, "passkey": item}


@router.delete("/passkeys/{credential_id}")
async def remove_passkey(credential_id: str, request: Request):
    if not request_is_authenticated(request):
        return api_error("Session requise pour supprimer une passkey.", 401)
    try:
        deleted = await asyncio.to_thread(delete_passkey, credential_id)
    except PasskeyError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Suppression de la passkey temporairement indisponible.", 503)
    if not deleted:
        return api_error("Passkey introuvable.", 404)
    return {"ok": True}


@router.post("/passkeys/authenticate/options")
async def begin_passkey_authentication(request: Request):
    context = login_client_context(request)
    status = await asyncio.to_thread(login_attempt_status, context["client_hash"])
    if not status["allowed"]:
        return api_error("Cet appareil est actuellement bloqué.", 403)
    try:
        result = await asyncio.to_thread(authentication_options, context["client_hash"])
    except PasskeyError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Face ID/passkey temporairement indisponible.", 503)
    return {"ok": True, **result}


@router.post("/passkeys/authenticate/verify")
async def finish_passkey_authentication(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict,
):
    context = login_client_context(request)
    status = await asyncio.to_thread(login_attempt_status, context["client_hash"])
    if not status["allowed"]:
        return api_error("Cet appareil est actuellement bloqué.", 403)
    credential = payload.get("credential")
    if not isinstance(credential, dict):
        return api_error("Réponse passkey invalide.")
    try:
        await asyncio.to_thread(
            verify_authentication,
            str(payload.get("ceremony_id") or ""),
            credential,
            context["client_hash"],
        )
        await asyncio.to_thread(record_login_success, context)
    except PasskeyError as exc:
        return api_error(str(exc), 401)
    except Exception:
        return api_error("Connexion Face ID/passkey temporairement indisponible.", 503)
    response = JSONResponse(
        {"ok": True, "next": safe_next_path(str(payload.get("next") or "/"))}
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(),
        max_age=session_max_age_seconds(),
        httponly=True,
        secure=settings.studio_cookie_secure,
        samesite="lax",
        path="/",
    )
    background_tasks.add_task(
        send_notification,
        preference="studio_login",
        title="Connexion au Studio",
        body="Une connexion Face ID/passkey vient d’être effectuée.",
        url="/?tab=settings",
        tag="studio-login",
    )
    return response


@router.get("/instagram/token-health")
async def instagram_token_health():
    try:
        health = await asyncio.to_thread(token_health)
    except Exception:
        return api_error("État du token Instagram temporairement indisponible.", 503)
    return {"ok": True, **health}


@router.get("/analytics/dashboard")
async def analytics_dashboard(period_days: int = 30):
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)
    try:
        dashboard = await asyncio.to_thread(
            build_analytics_dashboard,
            period_days=period_days,
        )
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


@router.get("/analytics/sync-progress")
async def analytics_sync_progress():
    return {"ok": True, "progress": get_analytics_sync_progress()}


@router.post("/analytics/assistant")
async def run_analytics_assistant(period_days: int = 30):
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)
    try:
        dashboard = await asyncio.to_thread(
            build_analytics_dashboard,
            period_days=period_days,
        )
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


@router.post("/analytics/content-ideas")
async def create_growth_content_ideas(payload: dict, period_days: int = 30):
    brief = str(payload.get("brief") or "").strip()
    if len(brief) > 800:
        return api_error("Le brief est trop long (800 caractères maximum).")
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)
    try:
        dashboard = await asyncio.to_thread(
            build_analytics_dashboard,
            period_days=period_days,
        )
        summary = dashboard.get("summary") or {}
        if int(summary.get("media_count", 0)) < 3:
            return api_error(
                "Synchronise au moins 3 publications avant de générer des idées.",
                409,
            )
        report = await generate_growth_content_ideas(dashboard, brief)
        await asyncio.to_thread(
            save_content_ideas,
            report,
            brief,
            (dashboard.get("sync") or {}).get("last_synced_at"),
            settings.cerebras_model,
        )
    except CerebrasError as exc:
        return api_error(str(exc), 502)
    except AnalyticsError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Générateur d’idées temporairement indisponible.", 503)
    return {"ok": True, "report": report, "model": settings.cerebras_model}


@router.get("/analytics/assistant/chat")
async def analytics_assistant_history():
    try:
        messages = await asyncio.to_thread(list_assistant_messages)
    except AnalyticsError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Historique de l’assistant temporairement indisponible.", 503)
    return {"ok": True, "messages": messages}


@router.post("/analytics/assistant/chat")
async def ask_analytics_assistant(payload: dict, period_days: int = 30):
    question = str(payload.get("message") or "").strip()
    if not question:
        return api_error("Écris une question pour l’assistant.")
    if len(question) > 1200:
        return api_error("La question est trop longue (1 200 caractères maximum).")
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)
    try:
        dashboard = await asyncio.to_thread(
            build_analytics_dashboard,
            period_days=period_days,
        )
        if int((dashboard.get("summary") or {}).get("media_count", 0)) < 3:
            return api_error("Synchronise au moins 3 publications avant de discuter.", 409)
        answer = await chat_instagram_performance(dashboard, question)
        await asyncio.to_thread(
            save_assistant_exchange,
            question,
            answer,
            int((dashboard.get("period_comparison") or {}).get("days", 30)),
        )
    except CerebrasError as exc:
        return api_error(str(exc), 502)
    except AnalyticsError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Assistant Groq temporairement indisponible.", 503)
    return {"ok": True, "answer": answer, "model": settings.cerebras_model}


@router.delete("/analytics/assistant/chat")
async def delete_analytics_assistant_history():
    try:
        deleted = await asyncio.to_thread(clear_assistant_messages)
    except AnalyticsError as exc:
        return api_error(str(exc), 409)
    except Exception:
        return api_error("Suppression de l’historique temporairement indisponible.", 503)
    return {"ok": True, "deleted": deleted}


@router.post("/publications/preflight")
async def publication_preflight(payload: dict):
    media_kind = str(payload.get("media_kind", "reel")).strip().lower()
    if media_kind not in {"reel", "photo", "carousel", "story"}:
        return api_error("Type de publication invalide.")
    if media_kind == "story" and not settings.enable_instagram_stories:
        return api_error("Les Stories sont désactivées sur ce déploiement.", 409)
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
        total_bytes = sum(
            int(item.get("bytes", 0)) + int(item.get("muted_bytes", 0))
            for item in documents
        )
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
        providers: dict[str, dict[str, int]] = {}
        for item in documents:
            provider = stored_media_provider(item)
            summary = providers.setdefault(provider, {"count": 0, "bytes": 0})
            summary["count"] += 1
            summary["bytes"] += int(item.get("bytes", 0)) + int(
                item.get("muted_bytes", 0)
            )
        return documents, total_bytes, providers

    try:
        documents, total_bytes, providers = await asyncio.to_thread(query)
    except Exception:
        return api_error("Bibliothèque MongoDB indisponible.", 503)

    return {
        "ok": True,
        "items": [serialize_document(item) for item in documents],
        "total_bytes": total_bytes,
        "providers": providers,
    }


@router.post("/library/promote")
async def promote_media_to_library(payload: dict):
    if not database_configured() or not media_storage_configured():
        return api_error("MongoDB ou le stockage média n’est pas configuré.", 503)

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

    stored_media = None
    document = None
    try:
        source_path = local_media_path(media_url)
        if source_path is not None:
            stored_media = await asyncio.to_thread(
                store_media_path,
                source_path,
                source_path.name,
                media_type,
                bool(payload.get("mute_audio")),
            )
        else:
            stored_media = await asyncio.to_thread(
                store_media_url,
                media_url,
                media_type,
                bool(payload.get("mute_audio")),
            )

        document = {
            "storage_provider": stored_media["storage_provider"],
            "storage_key": stored_media["storage_key"],
            "secure_url": stored_media["secure_url"],
            "thumbnail_url": stored_media["thumbnail_url"],
            "bytes": stored_media["bytes"],
            "duration": stored_media["duration"],
            "format": stored_media["format"],
            "width": stored_media["width"],
            "height": stored_media["height"],
            "original_filename": stored_media["original_filename"],
            "description": str(payload.get("description", "")).strip()[:500],
            "media_type": stored_media["media_type"],
            "resource_type": stored_media["resource_type"],
            "muted": bool(stored_media.get("muted")),
            "created_at": utc_now(),
        }
        if stored_media.get("public_id"):
            document["cloudinary_public_id"] = stored_media["public_id"]
        result = await asyncio.to_thread(database().media.insert_one, document)
        document["_id"] = result.inserted_id
    except MediaStorageError as exc:
        return api_error(str(exc), 502)
    except Exception:
        cleanup_media = document or stored_media
        if cleanup_media:
            try:
                await asyncio.to_thread(delete_stored_media, cleanup_media)
            except Exception:
                pass
        return api_error("Impossible d’enregistrer le média programmé.", 503)

    return {
        "ok": True,
        "url": stored_media["publication_url"],
        "media": serialize_document(document),
    }


@router.post("/media/mute")
async def mute_media(payload: dict):
    video_url = str(payload.get("video_url", "")).strip()
    library_id = str(payload.get("library_id", "")).strip()

    if library_id and database_configured():
        try:
            media = await asyncio.to_thread(
                database().media.find_one,
                {"_id": object_id(library_id)},
            )
            if media:
                prepared = await asyncio.to_thread(prepare_muted_media, media)
                if prepared["updates"]:
                    await asyncio.to_thread(
                        database().media.update_one,
                        {"_id": media["_id"]},
                        {"$set": prepared["updates"]},
                    )
                return {
                    "ok": True,
                    "url": prepared["url"],
                    "storage": stored_media_provider(media),
                }
        except MediaStorageError as exc:
            return api_error(str(exc), 502)
        except Exception:
            return api_error("Bibliothèque MongoDB indisponible.", 503)

    try:
        result = await asyncio.to_thread(mute_local_video, video_url)
    except LocalMediaError as exc:
        return api_error(str(exc))
    return {"ok": True, **result, "storage": "temporary"}


@router.delete("/library/{media_id}")
async def remove_library_media(media_id: str):
    if not database_configured():
        return api_error("MongoDB n’est pas configuré.", 503)
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
        if not active:
            active = database().autopilot_queue.find_one(
                {
                    "library_ids": media_id,
                    "status": {"$in": list(AUTOPILOT_ACTIVE_STATUSES)},
                }
            )
        return media, active

    media, active = await asyncio.to_thread(load)
    if not media:
        return api_error("Média introuvable.", 404)
    if active:
        return api_error(
            "Ce média est utilisé par une publication programmée ou par Auto-pilot. Retire-la d’abord.",
            409,
        )

    try:
        await asyncio.to_thread(delete_stored_media, media)
        await asyncio.to_thread(database().media.delete_one, {"_id": identifier})
    except MediaStorageError as exc:
        return api_error(str(exc), 502)
    return {"ok": True}


@router.post("/publications")
async def create_publication(payload: dict):
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)

    media_kind = str(payload.get("media_kind", "reel")).strip().lower()
    if media_kind not in {"reel", "photo", "carousel", "story"}:
        return api_error("Type de publication invalide.")
    if media_kind == "story" and not settings.enable_instagram_stories:
        return api_error("Les Stories sont désactivées sur ce déploiement.", 409)
    try:
        media_items = publication_media_items(payload, media_kind)
    except ValueError as exc:
        return api_error(str(exc))

    library_ids = [item["library_id"] for item in media_items if item["library_id"]]
    library_id = library_ids[0] if len(library_ids) == 1 else None
    mute_audio = (
        bool(payload.get("mute_audio"))
        if media_kind == "reel"
        or (media_kind == "story" and media_items[0]["media_type"] == "video")
        else False
    )

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
    if media_kind == "story" and workflow != "auto_publish":
        return api_error(
            "La musique et les stickers de Story ne sont pas disponibles via l’API Meta."
        )

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

    if mute_audio and library_id:
        try:
            media = await asyncio.to_thread(
                database().media.find_one,
                {"_id": object_id(library_id)},
            )
            if media:
                prepared = await asyncio.to_thread(prepare_muted_media, media)
                media_items[0]["url"] = prepared["url"]
                if prepared["updates"]:
                    await asyncio.to_thread(
                        database().media.update_one,
                        {"_id": media["_id"]},
                        {"$set": prepared["updates"]},
                    )
        except MediaStorageError as exc:
            await asyncio.to_thread(release_publication_claim, dedupe_key)
            return api_error(str(exc), 502)
        except Exception:
            await asyncio.to_thread(release_publication_claim, dedupe_key)
            return api_error("Impossible de préparer la version sans son.", 503)

    scheduled_value = payload.get("scheduled_for")
    if scheduled_value:
        if len(library_ids) != len(media_items):
            await asyncio.to_thread(release_publication_claim, dedupe_key)
            return api_error(
                "Chaque média programmé doit d’abord être enregistré dans le stockage durable."
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

    is_story_video = media_kind == "story" and media_items[0]["media_type"] == "video"
    is_story_image = media_kind == "story" and media_items[0]["media_type"] == "image"
    document = {
        "title": str(payload.get("title", "Publication Instagram")).strip()[:120]
        or "Publication Instagram",
        "media_kind": media_kind,
        "media_items": media_items,
        "library_ids": library_ids,
        "library_id": library_id,
        "video_url": media_items[0]["url"] if media_kind == "reel" or is_story_video else "",
        "image_url": media_items[0]["url"] if media_kind == "photo" or is_story_image else "",
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
    await publish_calendar_change(
        action="created",
        publication_id=result.inserted_id,
        status=status,
    )
    return {"ok": True, "publication": serialize_document(document)}


AUTOPILOT_ACTIVE_STATUSES = {
    "queued",
    "analyzing",
    "analyzed",
    "analysis_failed",
    "planned",
}


def _autopilot_payload(payload: dict, media_kind: str, media_items: list[dict]) -> dict:
    return {
        "title": str(payload.get("title") or "Publication Instagram").strip()[:120]
        or "Publication Instagram",
        "description": str(payload.get("description") or "").strip()[:1200],
        "location": str(payload.get("location") or "").strip()[:300],
        "device": str(payload.get("device") or "").strip()[:300],
        "media_kind": media_kind,
        "media_items": media_items,
        "video_url": str(payload.get("video_url") or "").strip(),
        "image_url": str(payload.get("image_url") or "").strip(),
        "library_id": str(payload.get("library_id") or "").strip(),
        "story_media_type": str(payload.get("story_media_type") or "").strip(),
        "thumbnail_url": str(payload.get("thumbnail_url") or "").strip(),
        "caption": str(payload.get("caption") or "").strip(),
        "hook": str(payload.get("hook") or "").strip(),
        "alt_text": str(payload.get("alt_text") or "").strip(),
        "publication_mode": str(payload.get("publication_mode") or "normal").lower(),
        "workflow": str(payload.get("workflow") or "auto_publish").lower(),
        "mute_audio": bool(payload.get("mute_audio")),
        "timezone": str(payload.get("timezone") or "Europe/Paris")[:80],
    }


@router.get("/autopilot/queue")
async def list_autopilot_queue():
    if not database_configured():
        return api_error("MONGODB_URI n’est pas configurée.", 503)
    try:
        documents = await asyncio.to_thread(
            lambda: list(database().autopilot_queue.find({}).sort("created_at", -1).limit(200))
        )
    except Exception:
        return api_error("File Auto-pilot temporairement indisponible.", 503)
    return {
        "ok": True,
        "items": [serialize_document(document) for document in documents],
        "vision_model": settings.cerebras_vision_model,
        "default_posts_per_week": settings.autopilot_default_posts_per_week,
    }


@router.post("/autopilot/queue")
async def add_autopilot_queue(payload: dict):
    if not database_configured() or not media_storage_configured():
        return api_error("MongoDB ou le stockage média n’est pas configuré.", 503)
    media_kind = str(payload.get("media_kind") or "reel").strip().lower()
    if media_kind not in {"reel", "photo", "carousel", "story"}:
        return api_error("Type de publication invalide.")
    if media_kind == "story" and not settings.enable_instagram_stories:
        return api_error("Les Stories sont désactivées sur ce déploiement.", 409)
    try:
        media_items = publication_media_items(payload, media_kind)
        publication_mode = str(payload.get("publication_mode") or "normal").lower()
        workflow = str(payload.get("workflow") or "auto_publish").lower()
        publication_checks(
            media_kind=media_kind,
            media_items=media_items,
            caption=str(payload.get("caption") or ""),
            publication_mode=publication_mode,
            workflow=workflow,
        )
    except ValueError as exc:
        return api_error(str(exc))
    library_ids = [str(item.get("library_id") or "") for item in media_items]
    if not all(library_ids):
        return api_error("Chaque média Auto-pilot doit être enregistré dans la bibliothèque.")
    try:
        identifiers = [object_id(value) for value in library_ids]
    except ValueError as exc:
        return api_error(str(exc))
    normalized = _autopilot_payload(payload, media_kind, media_items)
    queue_key = publication_fingerprint(
        media_kind=media_kind,
        media_items=media_items,
        caption=normalized["caption"],
        publication_mode=normalized["publication_mode"],
        workflow=normalized["workflow"],
        scheduled_for="autopilot",
    )

    def insert():
        if database().media.count_documents({"_id": {"$in": identifiers}}) != len(set(identifiers)):
            raise ValueError("Un média de la file n’existe plus dans la bibliothèque.")
        duplicate = database().autopilot_queue.find_one(
            {"queue_key": queue_key, "status": {"$in": list(AUTOPILOT_ACTIVE_STATUSES)}}
        )
        if duplicate:
            raise RuntimeError("duplicate")
        now = utc_now()
        document = {
            **normalized,
            "payload": normalized,
            "library_ids": library_ids,
            "queue_key": queue_key,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
        }
        result = database().autopilot_queue.insert_one(document)
        document["_id"] = result.inserted_id
        return document

    try:
        document = await asyncio.to_thread(insert)
    except ValueError as exc:
        return api_error(str(exc), 409)
    except RuntimeError as exc:
        if str(exc) == "duplicate":
            return api_error("Cette création est déjà dans la file Auto-pilot.", 409)
        return api_error("Impossible d’ajouter la création à Auto-pilot.", 503)
    except Exception:
        return api_error("Impossible d’ajouter la création à Auto-pilot.", 503)
    return {"ok": True, "item": serialize_document(document)}


@router.delete("/autopilot/queue/{queue_id}")
async def remove_autopilot_queue_item(queue_id: str):
    if not database_configured():
        return api_error("MongoDB n’est pas configuré.", 503)
    try:
        identifier = object_id(queue_id)
    except ValueError as exc:
        return api_error(str(exc))
    item = await asyncio.to_thread(database().autopilot_queue.find_one, {"_id": identifier})
    if not item:
        return api_error("Élément Auto-pilot introuvable.", 404)
    if item.get("status") == "scheduled":
        return api_error("Cette publication est déjà programmée. Annule-la depuis le calendrier.", 409)
    await asyncio.to_thread(database().autopilot_queue.delete_one, {"_id": identifier})
    return {"ok": True}


@router.post("/autopilot/queue/{queue_id}/analyze")
async def analyze_autopilot_queue_item(queue_id: str):
    if not database_configured():
        return api_error("MongoDB n’est pas configuré.", 503)
    try:
        identifier = object_id(queue_id)
    except ValueError as exc:
        return api_error(str(exc))
    item = await asyncio.to_thread(database().autopilot_queue.find_one, {"_id": identifier})
    if not item:
        return api_error("Élément Auto-pilot introuvable.", 404)
    if item.get("status") == "scheduled":
        return api_error("Cette publication est déjà programmée.", 409)
    await asyncio.to_thread(
        database().autopilot_queue.update_one,
        {"_id": identifier},
        {"$set": {"status": "analyzing", "updated_at": utc_now()}, "$unset": {"last_error": ""}},
    )
    try:
        id_order = [object_id(value) for value in item.get("library_ids") or []]
        media_documents = await asyncio.to_thread(
            lambda: list(database().media.find({"_id": {"$in": id_order}}))
        )
        media_by_id = {document["_id"]: document for document in media_documents}
        media_documents = [media_by_id[value] for value in id_order if value in media_by_id]
        if len(media_documents) != len(id_order):
            raise AutopilotError("Un média de la file n’existe plus dans la bibliothèque.")
        analysis = await analyze_autopilot_media(
            media_documents,
            {
                "title": item.get("title"),
                "description": item.get("description"),
                "location": item.get("location"),
                "device": item.get("device"),
                "media_kind": item.get("media_kind"),
            },
        )
        analyzed_at = utc_now()
        await asyncio.to_thread(
            database().autopilot_queue.update_one,
            {"_id": identifier},
            {
                "$set": {
                    "status": "analyzed",
                    "visual_analysis": analysis,
                    "vision_model": settings.cerebras_vision_model,
                    "analyzed_at": analyzed_at,
                    "updated_at": analyzed_at,
                }
            },
        )
    except AutopilotError as exc:
        await asyncio.to_thread(
            database().autopilot_queue.update_one,
            {"_id": identifier},
            {"$set": {"status": "analysis_failed", "last_error": str(exc)[:700], "updated_at": utc_now()}},
        )
        return api_error(str(exc), 502)
    except Exception:
        message = "Analyse visuelle temporairement indisponible."
        await asyncio.to_thread(
            database().autopilot_queue.update_one,
            {"_id": identifier},
            {"$set": {"status": "analysis_failed", "last_error": message, "updated_at": utc_now()}},
        )
        return api_error(message, 503)
    return {"ok": True, "analysis": analysis, "model": settings.cerebras_vision_model}


@router.post("/autopilot/plan")
async def plan_autopilot_queue(payload: dict):
    if not database_configured():
        return api_error("MongoDB n’est pas configuré.", 503)
    try:
        posts_per_week = min(7, max(1, int(payload.get("posts_per_week") or settings.autopilot_default_posts_per_week)))
    except (TypeError, ValueError):
        return api_error("Fréquence Auto-pilot invalide.")
    timezone_name = str(payload.get("timezone") or "Europe/Paris")[:80]
    try:
        queue_items = await asyncio.to_thread(
            lambda: list(
                database().autopilot_queue.find(
                    {"status": {"$in": ["analyzed", "planned"]}}
                ).sort("created_at", 1).limit(100)
            )
        )
        if not queue_items:
            return api_error("Analyse d’abord les médias présents dans la file.", 409)
        try:
            dashboard = await asyncio.to_thread(build_analytics_dashboard, timezone_name, 90)
        except AnalyticsError:
            dashboard = {"summary": {}, "best_times": [], "automatic_findings": ["Historique insuffisant : créneaux prudents par défaut."]}
        existing_documents = await asyncio.to_thread(
            lambda: list(
                database().publications.find(
                    {"status": {"$in": ["scheduled", "publishing"]}, "scheduled_for": {"$gte": utc_now()}},
                    {"scheduled_for": 1},
                )
            )
        )
        plan = await generate_autopilot_plan(
            queue_items=queue_items,
            dashboard=dashboard,
            existing_dates=[item["scheduled_for"] for item in existing_documents if isinstance(item.get("scheduled_for"), datetime)],
            posts_per_week=posts_per_week,
            timezone_name=timezone_name,
        )
        planned_at = utc_now()
        proposals = {item["queue_id"]: item for item in plan["items"]}
        for item in queue_items:
            queue_id = str(item["_id"])
            proposal = dict(proposals[queue_id])
            proposal["scheduled_for"] = parse_datetime(proposal["scheduled_for"])
            await asyncio.to_thread(
                database().autopilot_queue.update_one,
                {"_id": item["_id"]},
                {"$set": {"status": "planned", "proposal": proposal, "planned_at": planned_at, "updated_at": planned_at}},
            )
    except AutopilotError as exc:
        return api_error(str(exc), 502)
    except Exception:
        return api_error("Création du planning Auto-pilot indisponible.", 503)
    return {"ok": True, "plan": plan, "model": settings.cerebras_model}


@router.post("/autopilot/queue/{queue_id}/approve")
async def approve_autopilot_queue_item(queue_id: str, payload: dict):
    if not database_configured():
        return api_error("MongoDB n’est pas configuré.", 503)
    try:
        identifier = object_id(queue_id)
    except ValueError as exc:
        return api_error(str(exc))
    item = await asyncio.to_thread(database().autopilot_queue.find_one, {"_id": identifier})
    if not item:
        return api_error("Élément Auto-pilot introuvable.", 404)
    if item.get("status") != "planned":
        return api_error("Cette proposition doit d’abord être analysée et planifiée.", 409)
    proposal = item.get("proposal") or {}
    scheduled_value = payload.get("scheduled_for") or proposal.get("scheduled_for")
    try:
        scheduled_for = parse_datetime(scheduled_value)
    except ValueError as exc:
        return api_error(str(exc))
    publication_payload = dict(item.get("payload") or {})
    publication_payload["scheduled_for"] = scheduled_for.isoformat()
    result = await create_publication(publication_payload)
    if isinstance(result, JSONResponse):
        return result
    publication = result.get("publication") or {}
    approved_at = utc_now()
    await asyncio.to_thread(
        database().autopilot_queue.update_one,
        {"_id": identifier, "status": "planned"},
        {
            "$set": {
                "status": "scheduled",
                "publication_id": publication.get("id"),
                "proposal.scheduled_for": scheduled_for,
                "approved_at": approved_at,
                "updated_at": approved_at,
            }
        },
    )
    return {"ok": True, "publication": publication}


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
    await publish_calendar_change(
        action="rescheduled",
        publication_id=identifier,
        status="scheduled",
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
    await publish_calendar_change(
        action="cancelled",
        publication_id=identifier,
        status="cancelled",
    )
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
