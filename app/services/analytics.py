import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.config import settings
from app.services.database import database, database_configured, utc_now
from app.services.instagram import api_url
from app.services.token_store import resolve_instagram_credentials


class AnalyticsError(RuntimeError):
    pass


COMMON_MEDIA_METRICS = (
    "views",
    "reach",
    "likes",
    "comments",
    "saved",
    "shares",
    "total_interactions",
)
REEL_MEDIA_METRICS = COMMON_MEDIA_METRICS + (
    "ig_reels_avg_watch_time",
    "ig_reels_video_view_total_time",
    "clips_replays_count",
    "reels_skip_rate",
)
MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,timestamp,permalink,"
    "thumbnail_url,username"
)
_sync_lock = asyncio.Lock()


def _safe_meta_message(response: httpx.Response, access_token: str) -> str:
    try:
        payload = response.json()
        error = payload.get("error") or {}
        message = str(error.get("message") or "")
        error_code = error.get("code")
    except (ValueError, AttributeError):
        message = response.text
        error_code = None
    message = message.replace(access_token, "[secret redacted]")[:700]
    lowered = message.lower()
    if error_code == 190 or any(word in lowered for word in ("expired", "invalid access token")):
        return "La connexion Instagram a expiré. Reconnecte Instagram depuis Réglages."
    if error_code in {10, 200} or any(word in lowered for word in ("permission", "insight")):
        return (
            "Instagram refuse l’accès aux statistiques. Active "
            "instagram_business_manage_insights dans Meta Developers, puis reconnecte Instagram."
        )
    return f"Statistiques Instagram indisponibles : {message or response.status_code}"


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    access_token: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_params = dict(params or {})
    safe_params["access_token"] = access_token
    response = await client.get(api_url(path), params=safe_params)
    if response.status_code >= 400:
        raise AnalyticsError(_safe_meta_message(response, access_token))
    try:
        payload = response.json()
    except ValueError as exc:
        raise AnalyticsError("Instagram a renvoyé des statistiques invalides.") from exc
    if not isinstance(payload, dict):
        raise AnalyticsError("Instagram a renvoyé des statistiques invalides.")
    return payload


async def list_instagram_media(
    *,
    user_id: str,
    access_token: str,
    max_media: int,
    client: httpx.AsyncClient,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    path = f"{user_id}/media"
    params: dict[str, Any] | None = {
        "fields": MEDIA_FIELDS,
        "limit": min(100, max_media),
    }
    after_cursor = ""
    while len(items) < max_media:
        if after_cursor:
            params["after"] = after_cursor
        payload = await _get_json(client, path, access_token, params)
        page = payload.get("data") or []
        if not isinstance(page, list):
            raise AnalyticsError("Instagram a renvoyé une liste de publications invalide.")
        items.extend(item for item in page if isinstance(item, dict) and item.get("id"))
        after_cursor = str(
            (((payload.get("paging") or {}).get("cursors") or {}).get("after") or "")
        )
        if not after_cursor or not page:
            break
    return items[:max_media]


def _metric_number(item: dict[str, Any]) -> int | float | None:
    total = item.get("total_value")
    if isinstance(total, dict):
        value = total.get("value")
        if isinstance(value, (int, float)):
            return value
    values = item.get("values") or []
    if isinstance(values, list) and values:
        value = values[-1].get("value") if isinstance(values[-1], dict) else None
        if isinstance(value, (int, float)):
            return value
    return None


def normalize_insights(payload: dict[str, Any]) -> dict[str, int | float]:
    metrics: dict[str, int | float] = {}
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        value = _metric_number(item)
        if value is not None:
            metrics[str(item["name"])] = value
    return metrics


async def fetch_media_insights(
    *,
    media: dict[str, Any],
    access_token: str,
    client: httpx.AsyncClient,
) -> dict[str, int | float]:
    is_reel = (
        str(media.get("media_product_type", "")).upper() == "REELS"
        or str(media.get("media_type", "")).upper() == "VIDEO"
    )
    candidates = REEL_MEDIA_METRICS if is_reel else COMMON_MEDIA_METRICS
    try:
        payload = await _get_json(
            client,
            f"{media['id']}/insights",
            access_token,
            {"metric": ",".join(candidates)},
        )
        return normalize_insights(payload)
    except AnalyticsError as grouped_error:
        if "Active instagram_business_manage_insights" in str(grouped_error):
            raise

    # Meta fait évoluer les métriques par type de média. Une métrique devenue
    # indisponible ne doit pas empêcher de conserver toutes les autres.
    metrics: dict[str, int | float] = {}
    last_error: AnalyticsError | None = None
    for metric in candidates:
        try:
            payload = await _get_json(
                client,
                f"{media['id']}/insights",
                access_token,
                {"metric": metric},
            )
            metrics.update(normalize_insights(payload))
        except AnalyticsError as exc:
            last_error = exc
            if "Active instagram_business_manage_insights" in str(exc):
                raise
    if not metrics and last_error:
        raise last_error
    return metrics


def _parse_timestamp(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _media_kind(media: dict[str, Any]) -> str:
    media_type = str(media.get("media_type", "")).upper()
    product_type = str(media.get("media_product_type", "")).upper()
    if media_type == "CAROUSEL_ALBUM":
        return "carousel"
    if product_type == "REELS" or media_type == "VIDEO":
        return "reel"
    return "photo"


def _caption_hook(caption: str) -> str:
    for line in caption.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:180]
    return ""


def _https_url(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.startswith("https://") else ""


def _store_media_snapshot(
    media: dict[str, Any],
    metrics: dict[str, int | float],
    error: str = "",
) -> None:
    db = database()
    now = utc_now()
    media_id = str(media["id"])
    caption = str(media.get("caption", ""))
    publication = db.publications.find_one(
        {"instagram_media_id": media_id},
        {"hook": 1, "title": 1},
    )
    existing = db.instagram_media.find_one({"_id": media_id}) or {}
    previous_metrics = existing.get("latest_metrics") or {}
    hook = str((publication or {}).get("hook") or _caption_hook(caption))
    title = str((publication or {}).get("title") or hook or "Publication Instagram")[:180]
    update: dict[str, Any] = {
        "caption": caption,
        "hook": hook,
        "title": title,
        "media_kind": _media_kind(media),
        "media_type": str(media.get("media_type", "")),
        "media_product_type": str(media.get("media_product_type", "")),
        "timestamp": _parse_timestamp(media.get("timestamp")),
        "permalink": _https_url(media.get("permalink")),
        "thumbnail_url": _https_url(media.get("thumbnail_url")),
        "username": str(media.get("username", "")),
        "last_synced_at": now,
        "analytics_error": error[:500],
    }
    if metrics or not previous_metrics:
        update["latest_metrics"] = metrics
    if metrics and previous_metrics and metrics != previous_metrics:
        update["previous_metrics"] = previous_metrics
        update["previous_synced_at"] = existing.get("last_synced_at")
    db.instagram_media.update_one(
        {"_id": media_id},
        {
            "$set": update,
            "$setOnInsert": {"first_seen_at": now},
        },
        upsert=True,
    )
    if metrics and metrics != previous_metrics:
        db.instagram_insight_snapshots.insert_one(
            {
                "media_id": media_id,
                "captured_at": now,
                "metrics": metrics,
            }
        )
    if publication and metrics:
        db.publications.update_one(
            {"instagram_media_id": media_id},
            {"$set": {"latest_metrics": metrics, "insights_synced_at": now}},
        )


async def sync_instagram_analytics(max_media: int | None = None) -> dict[str, Any]:
    if not database_configured():
        raise AnalyticsError("MONGODB_URI est nécessaire pour enregistrer les statistiques.")
    if _sync_lock.locked():
        raise AnalyticsError("Une synchronisation des statistiques est déjà en cours.")
    user_id, access_token = await resolve_instagram_credentials()
    if not user_id or not access_token:
        raise AnalyticsError("Connecte Instagram avant de synchroniser les statistiques.")

    maximum = max(1, min(int(max_media or settings.analytics_max_media), 250))
    async with _sync_lock:
        async with httpx.AsyncClient(timeout=35) as client:
            media_items = await list_instagram_media(
                user_id=user_id,
                access_token=access_token,
                max_media=maximum,
                client=client,
            )
            semaphore = asyncio.Semaphore(4)

            async def collect(item: dict[str, Any]):
                async with semaphore:
                    try:
                        return item, await fetch_media_insights(
                            media=item,
                            access_token=access_token,
                            client=client,
                        ), ""
                    except AnalyticsError as exc:
                        return item, {}, str(exc)

            collected = await asyncio.gather(*(collect(item) for item in media_items))

        succeeded = 0
        failed = 0
        first_error = ""
        for item, metrics, error in collected:
            await asyncio.to_thread(_store_media_snapshot, item, metrics, error)
            if metrics:
                succeeded += 1
            elif error:
                failed += 1
                first_error = first_error or error
        now = utc_now()
        state = {
            "last_synced_at": now,
            "media_found": len(media_items),
            "metrics_updated": succeeded,
            "metrics_failed": failed,
            "last_error": first_error,
            "permission_required": bool(failed and not succeeded),
        }
        await asyncio.to_thread(
            database().analytics_state.update_one,
            {"_id": "primary"},
            {"$set": state},
            upsert=True,
        )
        return state


def _number(metrics: dict[str, Any], *names: str) -> float:
    for name in names:
        value = metrics.get(name)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _post_values(document: dict[str, Any]) -> dict[str, float]:
    metrics = document.get("latest_metrics") or {}
    views = _number(metrics, "views", "plays", "ig_reels_aggregated_all_plays_count")
    reach = _number(metrics, "reach")
    interactions = _number(metrics, "total_interactions")
    if not interactions:
        interactions = sum(_number(metrics, name) for name in ("likes", "comments", "saved", "shares"))
    denominator = reach or views
    engagement_rate = interactions / denominator * 100 if denominator else 0.0
    return {
        "views": views,
        "reach": reach,
        "interactions": interactions,
        "engagement_rate": engagement_rate,
    }


def _iso(value: Any) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else None


def build_analytics_dashboard(timezone_name: str = "Europe/Paris") -> dict[str, Any]:
    if not database_configured():
        raise AnalyticsError("MONGODB_URI est nécessaire pour afficher les statistiques.")
    db = database()
    documents = list(db.instagram_media.find({}).sort("timestamp", -1).limit(500))
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_timezone = ZoneInfo("Europe/Paris")

    totals = {"views": 0.0, "reach": 0.0, "interactions": 0.0}
    kind_counts: defaultdict[str, int] = defaultdict(int)
    kind_performance: defaultdict[str, list[dict[str, float]]] = defaultdict(list)
    hook_performance: defaultdict[str, list[dict[str, float]]] = defaultdict(list)
    time_groups: defaultdict[tuple[int, int], list[dict[str, float]]] = defaultdict(list)
    posts: list[dict[str, Any]] = []
    for document in documents:
        values = _post_values(document)
        for key in totals:
            totals[key] += values[key]
        kind = str(document.get("media_kind", "unknown"))
        kind_counts[kind] += 1
        kind_performance[kind].append(values)
        hook = str(document.get("hook") or "").strip()
        if hook:
            hook_performance["question" if "?" in hook else "affirmation"].append(values)
            hook_performance["court" if len(hook) <= 60 else "long"].append(values)
            hook_performance["avec_nombre" if any(char.isdigit() for char in hook) else "sans_nombre"].append(values)
        timestamp = document.get("timestamp")
        if isinstance(timestamp, datetime):
            local_date = timestamp.astimezone(local_timezone)
            time_groups[(local_date.weekday(), local_date.hour)].append(values)
        previous_metrics = document.get("previous_metrics")
        previous_values = _post_values({"latest_metrics": previous_metrics or {}})
        posts.append(
            {
                "id": str(document.get("_id")),
                "title": str(document.get("title") or "Publication Instagram"),
                "hook": str(document.get("hook") or ""),
                "media_kind": str(document.get("media_kind") or "unknown"),
                "timestamp": _iso(timestamp),
                "permalink": str(document.get("permalink") or ""),
                "thumbnail_url": str(document.get("thumbnail_url") or ""),
                **values,
                "delta_views": values["views"] - previous_values["views"] if previous_metrics is not None else None,
                "delta_interactions": values["interactions"] - previous_values["interactions"] if previous_metrics is not None else None,
            }
        )

    weekday_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    best_times = []
    for (weekday, hour), samples in time_groups.items():
        best_times.append(
            {
                "weekday": weekday_names[weekday],
                "hour": hour,
                "count": len(samples),
                "avg_engagement_rate": sum(x["engagement_rate"] for x in samples) / len(samples),
                "avg_views": sum(x["views"] for x in samples) / len(samples),
            }
        )
    best_times.sort(key=lambda item: (item["avg_engagement_rate"], item["avg_views"], item["count"]), reverse=True)
    posts.sort(key=lambda item: (item["engagement_rate"], item["views"]), reverse=True)
    denominator = totals["reach"] or totals["views"]
    automatic_findings: list[str] = []
    if best_times:
        best = best_times[0]
        automatic_findings.append(
            f"Meilleur créneau observé : {best['weekday']} vers {best['hour']:02d} h "
            f"sur {best['count']} publication(s)."
        )
    kind_labels = {"reel": "Reels", "photo": "Photos", "carousel": "Carrousels"}
    kind_results = []
    for kind, samples in kind_performance.items():
        kind_results.append(
            (
                sum(sample["engagement_rate"] for sample in samples) / len(samples),
                sum(sample["views"] for sample in samples) / len(samples),
                kind,
                len(samples),
            )
        )
    if kind_results:
        _, _, best_kind, count = max(kind_results)
        automatic_findings.append(
            f"Format le plus performant dans cet historique : "
            f"{kind_labels.get(best_kind, best_kind)} ({count} publication(s))."
        )
    hook_results = []
    for pattern, samples in hook_performance.items():
        if len(samples) >= 2:
            hook_results.append(
                (
                    sum(sample["engagement_rate"] for sample in samples) / len(samples),
                    pattern,
                    len(samples),
                )
            )
    if hook_results:
        _, pattern, count = max(hook_results)
        pattern_labels = {
            "question": "les hooks sous forme de question",
            "affirmation": "les hooks affirmatifs",
            "court": "les hooks courts (60 caractères maximum)",
            "long": "les hooks longs",
            "avec_nombre": "les hooks contenant un nombre",
            "sans_nombre": "les hooks sans nombre",
        }
        automatic_findings.append(
            f"Tendance d’accroche observée : {pattern_labels.get(pattern, pattern)} "
            f"arrive en tête sur {count} publication(s)."
        )
    if len(documents) < 10:
        automatic_findings.append(
            "Échantillon encore limité : interprète ces tendances comme des pistes, pas comme des certitudes."
        )
    state = db.analytics_state.find_one({"_id": "primary"}) or {}
    report = db.analytics_reports.find_one({"_id": "latest"}) or {}
    return {
        "summary": {
            "media_count": len(documents),
            "reels": kind_counts["reel"],
            "photos": kind_counts["photo"],
            "carousels": kind_counts["carousel"],
            "views": int(totals["views"]),
            "reach": int(totals["reach"]),
            "interactions": int(totals["interactions"]),
            "engagement_rate": totals["interactions"] / denominator * 100 if denominator else 0.0,
        },
        "best_times": best_times[:8],
        "top_posts": posts[:20],
        "automatic_findings": automatic_findings,
        "sync": {
            "last_synced_at": _iso(state.get("last_synced_at")),
            "media_found": int(state.get("media_found", len(documents))),
            "metrics_updated": int(state.get("metrics_updated", 0)),
            "metrics_failed": int(state.get("metrics_failed", 0)),
            "last_error": str(state.get("last_error", "")),
            "permission_required": bool(state.get("permission_required", False)),
        },
        "assistant_report": report.get("report"),
        "assistant_report_created_at": _iso(report.get("created_at")),
    }


def save_analytics_report(
    report: dict[str, Any],
    based_on_sync_at: str | None,
    model: str,
) -> None:
    database().analytics_reports.update_one(
        {"_id": "latest"},
        {
            "$set": {
                "report": report,
                "based_on_sync_at": based_on_sync_at,
                "model": model,
                "created_at": utc_now(),
            }
        },
        upsert=True,
    )
