import asyncio
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
_sync_progress_lock = threading.Lock()
_sync_progress: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "percent": 0.0,
    "current": 0,
    "total": 0,
    "message": "Prêt à synchroniser.",
    "started_at": None,
    "finished_at": None,
}
PERIOD_DAYS_OPTIONS = {7, 30, 90}


def _set_sync_progress(**values: Any) -> None:
    with _sync_progress_lock:
        _sync_progress.update(values)


def get_analytics_sync_progress() -> dict[str, Any]:
    with _sync_progress_lock:
        return dict(_sync_progress)


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
    started_at = utc_now()
    _set_sync_progress(
        running=True,
        phase="preparing",
        percent=1.0,
        current=0,
        total=0,
        message="Connexion à Instagram…",
        started_at=_iso(started_at),
        finished_at=None,
    )
    try:
        user_id, access_token = await resolve_instagram_credentials()
        if not user_id or not access_token:
            raise AnalyticsError("Connecte Instagram avant de synchroniser les statistiques.")

        maximum = max(1, min(int(max_media or settings.analytics_max_media), 250))
        async with _sync_lock:
            _set_sync_progress(
                phase="listing",
                percent=4.0,
                message="Récupération de la liste des publications…",
            )
            async with httpx.AsyncClient(timeout=35) as client:
                media_items = await list_instagram_media(
                    user_id=user_id,
                    access_token=access_token,
                    max_media=maximum,
                    client=client,
                )
                total = len(media_items)
                _set_sync_progress(
                    phase="insights",
                    percent=10.0 if total else 85.0,
                    current=0,
                    total=total,
                    message=f"Lecture des statistiques de {total} publication(s)…",
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

                collected = []
                tasks = [asyncio.create_task(collect(item)) for item in media_items]
                for completed, task in enumerate(asyncio.as_completed(tasks), start=1):
                    collected.append(await task)
                    _set_sync_progress(
                        percent=10.0 + (completed / total * 75.0 if total else 75.0),
                        current=completed,
                        message=f"Statistiques Instagram : {completed} / {total}",
                    )

            succeeded = 0
            failed = 0
            first_error = ""
            _set_sync_progress(
                phase="saving",
                percent=86.0,
                current=0,
                message="Enregistrement des statistiques…",
            )
            for saved, (item, metrics, error) in enumerate(collected, start=1):
                await asyncio.to_thread(_store_media_snapshot, item, metrics, error)
                if metrics:
                    succeeded += 1
                elif error:
                    failed += 1
                    first_error = first_error or error
                _set_sync_progress(
                    percent=86.0 + (saved / total * 13.0 if total else 13.0),
                    current=saved,
                    message=f"Enregistrement : {saved} / {total}",
                )
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
            _set_sync_progress(
                running=False,
                phase="complete",
                percent=100.0,
                current=total,
                message=f"Synchronisation terminée : {succeeded} publication(s) mise(s) à jour.",
                finished_at=_iso(now),
            )
            return state
    except Exception as exc:
        finished_at = utc_now()
        message = str(exc) if isinstance(exc, AnalyticsError) else "Synchronisation Instagram interrompue."
        _set_sync_progress(
            running=False,
            phase="failed",
            message=message,
            finished_at=_iso(finished_at),
        )
        raise


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
    likes = _number(metrics, "likes")
    comments = _number(metrics, "comments")
    saved = _number(metrics, "saved")
    shares = _number(metrics, "shares")
    interactions = _number(metrics, "total_interactions")
    if not interactions:
        interactions = likes + comments + saved + shares
    denominator = reach or views
    engagement_rate = interactions / denominator * 100 if denominator else 0.0
    return {
        "views": views,
        "reach": reach,
        "likes": likes,
        "comments": comments,
        "saved": saved,
        "shares": shares,
        "interactions": interactions,
        "engagement_rate": engagement_rate,
        "like_rate": likes / denominator * 100 if denominator else 0.0,
        "comment_rate": comments / denominator * 100 if denominator else 0.0,
        "save_rate": saved / denominator * 100 if denominator else 0.0,
        "share_rate": shares / denominator * 100 if denominator else 0.0,
        "views_per_reached_account": views / reach if reach else 0.0,
        "avg_watch_time_ms": _number(metrics, "ig_reels_avg_watch_time"),
        "total_watch_time_ms": _number(metrics, "ig_reels_video_view_total_time"),
        "replays": _number(metrics, "clips_replays_count"),
        "skip_rate": _number(metrics, "reels_skip_rate"),
    }


def _iso(value: Any) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else None


def _period_summary(samples: list[dict[str, Any]]) -> dict[str, int | float]:
    totals = {"views": 0.0, "reach": 0.0, "interactions": 0.0}
    for sample in samples:
        for key in totals:
            totals[key] += float(sample.get(key, 0) or 0)
    denominator = totals["reach"] or totals["views"]
    return {
        "media_count": len(samples),
        "views": int(totals["views"]),
        "reach": int(totals["reach"]),
        "interactions": int(totals["interactions"]),
        "engagement_rate": totals["interactions"] / denominator * 100 if denominator else 0.0,
    }


def _change_percent(current: float, previous: float) -> float | None:
    if not previous:
        return 0.0 if not current else None
    return (current - previous) / abs(previous) * 100


def _period_comparison(
    samples: list[dict[str, Any]],
    period_days: int,
    now: datetime,
) -> dict[str, Any]:
    current_start = now - timedelta(days=period_days)
    previous_start = current_start - timedelta(days=period_days)
    current_samples = [
        sample
        for sample in samples
        if isinstance(sample.get("timestamp"), datetime)
        and current_start <= sample["timestamp"] <= now
    ]
    previous_samples = [
        sample
        for sample in samples
        if isinstance(sample.get("timestamp"), datetime)
        and previous_start <= sample["timestamp"] < current_start
    ]
    current = _period_summary(current_samples)
    previous = _period_summary(previous_samples)
    metric_names = ("media_count", "views", "reach", "interactions", "engagement_rate")
    return {
        "days": period_days,
        "current": {
            "start": _iso(current_start),
            "end": _iso(now),
            **current,
        },
        "previous": {
            "start": _iso(previous_start),
            "end": _iso(current_start),
            **previous,
        },
        "changes": {
            name: _change_percent(float(current[name]), float(previous[name]))
            for name in metric_names
        },
    }


def _snapshot_growth_series(
    snapshots: list[dict[str, Any]],
    period_days: int,
    now: datetime,
    local_timezone: ZoneInfo,
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=period_days)
    latest_by_media: dict[str, dict[str, float]] = {}
    daily_points: dict[str, dict[str, Any]] = {}
    baseline_added = False
    has_pre_cutoff_state = False

    def totals() -> dict[str, int]:
        return {
            name: int(sum(values[name] for values in latest_by_media.values()))
            for name in ("views", "reach", "interactions")
        }

    for snapshot in snapshots:
        captured_at = snapshot.get("captured_at")
        metrics = snapshot.get("metrics")
        if not isinstance(captured_at, datetime) or not isinstance(metrics, dict):
            continue
        captured_at = captured_at.astimezone(timezone.utc)
        if captured_at > now:
            continue
        if captured_at >= cutoff and has_pre_cutoff_state and not baseline_added:
            daily_points["baseline"] = {
                "captured_at": _iso(cutoff),
                **totals(),
            }
            baseline_added = True
        latest_by_media[str(snapshot.get("media_id") or "unknown")] = _post_values(
            {"latest_metrics": metrics}
        )
        if captured_at < cutoff:
            has_pre_cutoff_state = True
            continue
        day_key = captured_at.astimezone(local_timezone).date().isoformat()
        daily_points[day_key] = {
            "captured_at": _iso(captured_at),
            **totals(),
        }

    points = sorted(daily_points.values(), key=lambda item: item["captured_at"] or "")
    if points:
        first = points[0]
        for point in points:
            point["delta_views"] = point["views"] - first["views"]
            point["delta_reach"] = point["reach"] - first["reach"]
            point["delta_interactions"] = point["interactions"] - first["interactions"]
    return points


def build_analytics_dashboard(
    timezone_name: str = "Europe/Paris",
    period_days: int = 30,
) -> dict[str, Any]:
    if not database_configured():
        raise AnalyticsError("MONGODB_URI est nécessaire pour afficher les statistiques.")
    db = database()
    documents = list(db.instagram_media.find({}).sort("timestamp", -1).limit(500))
    period_days = period_days if period_days in PERIOD_DAYS_OPTIONS else 30
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_timezone = ZoneInfo("Europe/Paris")

    totals = {"views": 0.0, "reach": 0.0, "interactions": 0.0}
    kind_counts: defaultdict[str, int] = defaultdict(int)
    kind_performance: defaultdict[str, list[dict[str, float]]] = defaultdict(list)
    hook_performance: defaultdict[str, list[dict[str, float]]] = defaultdict(list)
    time_groups: defaultdict[tuple[int, int], list[dict[str, float]]] = defaultdict(list)
    period_samples: list[dict[str, Any]] = []
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
        period_samples.append({"timestamp": timestamp, **values})
        if isinstance(timestamp, datetime):
            local_date = timestamp.astimezone(local_timezone)
            time_groups[(local_date.weekday(), local_date.hour)].append(values)
        previous_metrics = document.get("previous_metrics")
        previous_values = _post_values({"latest_metrics": previous_metrics or {}})
        latest_metrics = document.get("latest_metrics") or {}
        available_metrics = sorted(
            str(name)
            for name, value in latest_metrics.items()
            if isinstance(value, (int, float))
        )
        posts.append(
            {
                "id": str(document.get("_id")),
                "title": str(document.get("title") or "Publication Instagram"),
                "hook": str(document.get("hook") or ""),
                "media_kind": str(document.get("media_kind") or "unknown"),
                "media_product_type": str(document.get("media_product_type") or ""),
                "timestamp": _iso(timestamp),
                "permalink": str(document.get("permalink") or ""),
                "thumbnail_url": str(document.get("thumbnail_url") or ""),
                "available_metrics": available_metrics,
                "previous_synced_at": _iso(document.get("previous_synced_at")),
                **values,
                "delta_views": values["views"] - previous_values["views"] if previous_metrics is not None else None,
                "delta_reach": values["reach"] - previous_values["reach"] if previous_metrics is not None else None,
                "delta_likes": values["likes"] - previous_values["likes"] if previous_metrics is not None else None,
                "delta_comments": values["comments"] - previous_values["comments"] if previous_metrics is not None else None,
                "delta_saved": values["saved"] - previous_values["saved"] if previous_metrics is not None else None,
                "delta_shares": values["shares"] - previous_values["shares"] if previous_metrics is not None else None,
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
    now = utc_now().astimezone(timezone.utc)
    snapshot_collection = getattr(db, "instagram_insight_snapshots", None)
    snapshots = (
        list(snapshot_collection.find({}).sort("captured_at", -1).limit(10000))
        if snapshot_collection is not None
        else []
    )
    snapshots.reverse()
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
        "top_posts": posts[:100],
        "automatic_findings": automatic_findings,
        "period_comparison": _period_comparison(period_samples, period_days, now),
        "growth_series": _snapshot_growth_series(
            snapshots,
            period_days,
            now,
            local_timezone,
        ),
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


def list_assistant_messages(limit: int = 40) -> list[dict[str, Any]]:
    if not database_configured():
        raise AnalyticsError("MONGODB_URI est nécessaire pour l’historique de l’assistant.")
    documents = list(
        database().analytics_assistant_messages.find({})
        .sort("created_at", -1)
        .limit(max(1, min(limit, 100)))
    )
    documents.reverse()
    return [
        {
            "id": str(item.get("_id")),
            "role": str(item.get("role") or "assistant"),
            "content": str(item.get("content") or ""),
            "created_at": _iso(item.get("created_at")),
        }
        for item in documents
    ]


def save_assistant_exchange(question: str, answer: str, period_days: int) -> None:
    now = utc_now()
    expiration = now + timedelta(days=90)
    database().analytics_assistant_messages.insert_many(
        [
            {
                "role": "user",
                "content": question.strip()[:1200],
                "period_days": period_days,
                "created_at": now,
                "expires_at": expiration,
            },
            {
                "role": "assistant",
                "content": answer.strip()[:5000],
                "period_days": period_days,
                "created_at": now + timedelta(microseconds=1),
                "expires_at": expiration,
            },
        ]
    )


def clear_assistant_messages() -> int:
    if not database_configured():
        raise AnalyticsError("MONGODB_URI est nécessaire pour l’historique de l’assistant.")
    return int(database().analytics_assistant_messages.delete_many({}).deleted_count)
