import asyncio
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pymongo import ReturnDocument

from app.config import settings
from app.services.database import database, database_configured, ensure_indexes, utc_now
from app.services.instagram import (
    InstagramError,
    create_carousel_container,
    create_image_container,
    create_reel_container,
    publish_container,
    wait_until_ready,
)
from app.services.push_notifications import send_notification
from app.services.token_store import resolve_instagram_credentials


_scheduler: AsyncIOScheduler | None = None
_tick_lock = asyncio.Lock()
_indexes_ready = False


async def _send_token_health_alerts() -> None:
    now = utc_now()
    document = await asyncio.to_thread(
        database().instagram_credentials.find_one,
        {"_id": "primary"},
        {
            "encrypted_access_token": 0,
            "user_id": 0,
        },
    )
    if not document:
        return

    retry_cutoff = now - timedelta(hours=6)
    refresh_error_at = document.get("refresh_error_at")
    refresh_attempted_at = document.get("refresh_error_alert_attempted_at")
    refresh_sent_for = document.get("refresh_error_alert_sent_for")
    if (
        refresh_error_at
        and refresh_sent_for != refresh_error_at
        and (not refresh_attempted_at or refresh_attempted_at <= retry_cutoff)
    ):
        sent = await send_notification(
            preference="instagram_token",
            title="Token Instagram à vérifier",
            body="Le renouvellement automatique a échoué. Reconnecte Instagram dans les Réglages.",
            url="/?tab=settings",
            tag="instagram-token-refresh",
        )
        update = {"refresh_error_alert_attempted_at": now}
        if sent:
            update["refresh_error_alert_sent_for"] = refresh_error_at
        await asyncio.to_thread(
            database().instagram_credentials.update_one,
            {"_id": "primary"},
            {"$set": update},
        )

    expires_at = document.get("expires_at")
    refreshed_at = document.get("refreshed_at")
    expiry_attempted_at = document.get("expiry_alert_attempted_at")
    expiry_sent_for = document.get("expiry_alert_sent_for")
    if (
        expires_at
        and expires_at <= now + timedelta(days=7)
        and expiry_sent_for != refreshed_at
        and (not expiry_attempted_at or expiry_attempted_at <= retry_cutoff)
    ):
        remaining = max(0, int((expires_at - now).total_seconds() // 86400))
        sent = await send_notification(
            preference="instagram_token",
            title="Token Instagram bientôt expiré",
            body=f"Le token expire dans environ {remaining} jour(s). Reconnecte Instagram dans les Réglages.",
            url="/?tab=settings",
            tag="instagram-token-expiry",
        )
        update = {"expiry_alert_attempted_at": now}
        if sent:
            update["expiry_alert_sent_for"] = refreshed_at
        await asyncio.to_thread(
            database().instagram_credentials.update_one,
            {"_id": "primary"},
            {"$set": update},
        )


async def _send_upcoming_reminders() -> None:
    now = utc_now()
    horizon = now + timedelta(minutes=30)

    def find_items():
        return list(
            database().publications.find(
                {
                    "status": "scheduled",
                    "scheduled_for": {"$gt": now, "$lte": horizon},
                    "reminder_sent_at": {"$exists": False},
                }
            ).limit(20)
        )

    publications = await asyncio.to_thread(find_items)
    for publication in publications:
        scheduled_for = publication["scheduled_for"]
        try:
            local_timezone = ZoneInfo(publication.get("timezone", "Europe/Paris"))
        except ZoneInfoNotFoundError:
            local_timezone = ZoneInfo("Europe/Paris")
        title = publication.get("title") or "Publication Instagram"
        sent = await send_notification(
            preference="before_publication",
            title="Publication dans moins de 30 minutes",
            body=(
                f"{title} est programmée à "
                f"{scheduled_for.astimezone(local_timezone).strftime('%H:%M')}."
            ),
            url="/?tab=calendar",
            tag=f"publication-reminder-{publication['_id']}",
        )
        if sent:
            await asyncio.to_thread(
                database().publications.update_one,
                {"_id": publication["_id"]},
                {"$set": {"reminder_sent_at": utc_now()}},
            )


def _claim_due_publication():
    now = utc_now()
    return database().publications.find_one_and_update(
        {
            "status": "scheduled",
            "scheduled_for": {"$lte": now},
        },
        {
            "$set": {
                "status": "publishing",
                "started_at": now,
                "updated_at": now,
            },
            "$inc": {"attempts": 1},
        },
        sort=[("scheduled_for", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def _process_publication(publication: dict) -> None:
    publication_id = publication["_id"]
    title = publication.get("title") or "Publication Instagram"

    if publication.get("workflow") == "manual_music":
        await asyncio.to_thread(
            database().publications.update_one,
            {"_id": publication_id},
            {
                "$set": {
                    "status": "awaiting_manual",
                    "updated_at": utc_now(),
                }
            },
        )
        await send_notification(
            preference="manual_music",
            title="Prêt à finaliser dans Instagram",
            body=f"Ajoute la musique puis publie « {title} » dans l’application Instagram.",
            url=f"/?tab=calendar&publication={publication_id}",
            tag=f"manual-music-{publication_id}",
        )
        return

    try:
        user_id, access_token = await resolve_instagram_credentials()
        if not user_id or not access_token:
            raise InstagramError("Instagram n’est pas connecté.")

        media_kind = publication.get("media_kind", "reel")
        media_items = publication.get("media_items") or []
        creation_id = publication.get("creation_id")
        if not creation_id:
            if media_kind == "photo":
                image_url = (
                    media_items[0]["url"] if media_items else publication["image_url"]
                )
                creation_id = await create_image_container(
                    user_id=user_id,
                    access_token=access_token,
                    image_url=image_url,
                    caption=publication.get("caption", ""),
                )
            elif media_kind == "carousel":
                children: list[str] = []
                for item in media_items:
                    child_id = await create_image_container(
                        user_id=user_id,
                        access_token=access_token,
                        image_url=item["url"],
                        is_carousel_item=True,
                    )
                    await wait_until_ready(
                        creation_id=child_id,
                        access_token=access_token,
                    )
                    children.append(child_id)
                creation_id = await create_carousel_container(
                    user_id=user_id,
                    access_token=access_token,
                    children=children,
                    caption=publication.get("caption", ""),
                )
            else:
                creation_id = await create_reel_container(
                    user_id=user_id,
                    access_token=access_token,
                    video_url=publication["video_url"],
                    caption=publication.get("caption", ""),
                    trial=publication.get("publication_mode") == "trial",
                )
            await asyncio.to_thread(
                database().publications.update_one,
                {"_id": publication_id},
                {"$set": {"creation_id": creation_id, "updated_at": utc_now()}},
            )

        await wait_until_ready(
            creation_id=str(creation_id),
            access_token=access_token,
        )
        instagram_media_id = await publish_container(
            user_id=user_id,
            access_token=access_token,
            creation_id=str(creation_id),
        )

        await asyncio.to_thread(
            database().publications.update_one,
            {"_id": publication_id},
            {
                "$set": {
                    "status": "published",
                    "instagram_media_id": instagram_media_id,
                    "published_at": utc_now(),
                    "updated_at": utc_now(),
                },
                "$unset": {"last_error": ""},
            },
        )
        type_label = {
            "photo": "Photo",
            "carousel": "Carrousel",
        }.get(media_kind, "Reel")
        await send_notification(
            preference="published",
            title=f"{type_label} publié ✅",
            body=f"« {title} » a été publié sur Instagram.",
            url="/?tab=calendar",
            tag=f"published-{publication_id}",
        )
    except Exception as exc:
        safe_error = str(exc)[:800]
        await asyncio.to_thread(
            database().publications.update_one,
            {"_id": publication_id},
            {
                "$set": {
                    "status": "failed",
                    "last_error": safe_error,
                    "updated_at": utc_now(),
                }
            },
        )
        await send_notification(
            preference="failed",
            title="Échec de publication",
            body=f"« {title} » n’a pas pu être publié. Ouvre le Studio pour voir le détail.",
            url="/?tab=calendar",
            tag=f"failed-{publication_id}",
        )


async def scheduler_tick() -> None:
    global _indexes_ready
    if not database_configured() or _tick_lock.locked():
        return
    async with _tick_lock:
        try:
            if not _indexes_ready:
                await asyncio.to_thread(ensure_indexes)
                _indexes_ready = True
            await _send_token_health_alerts()
            await _send_upcoming_reminders()
            for _ in range(5):
                publication = await asyncio.to_thread(_claim_due_publication)
                if not publication:
                    break
                await _process_publication(publication)
        except Exception:
            # APScheduler ne doit pas remplir les logs si Atlas est momentanément
            # inaccessible. Le prochain tick réessaiera automatiquement.
            return


def _recover_interrupted_publications() -> None:
    cutoff = utc_now() - timedelta(minutes=15)
    database().publications.update_many(
        {"status": "publishing", "started_at": {"$lt": cutoff}},
        {
            "$set": {
                "status": "scheduled",
                "scheduled_for": utc_now(),
                "updated_at": utc_now(),
            }
        },
    )


def start_scheduler() -> AsyncIOScheduler | None:
    global _scheduler, _indexes_ready
    if not database_configured() or _scheduler is not None:
        return _scheduler

    try:
        ensure_indexes()
        _recover_interrupted_publications()
        _indexes_ready = True
    except Exception:
        _indexes_ready = False
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        scheduler_tick,
        "interval",
        seconds=max(15, settings.scheduler_interval_seconds),
        max_instances=1,
        coalesce=True,
        id="instagram-studio-scheduler",
    )
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
