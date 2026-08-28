import asyncio
import base64
import json
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import imageio_ffmpeg

from app.config import settings
from app.services.cerebras import (
    _extract_json,
    _groq_error,
    _reasoning_options,
    _response_format,
)
from app.services.database import utc_now
from app.services.r2_media import R2MediaError, download_object, download_public_media


class AutopilotError(RuntimeError):
    pass


AUTOPILOT_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "queue_id": {"type": "string"},
                    "scheduled_for": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "integer"},
                },
                "required": ["queue_id", "scheduled_for", "reason", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "items"],
    "additionalProperties": False,
}


def _download_stored_media(media: dict[str, Any], directory: Path) -> tuple[Path, bool]:
    media_type = str(media.get("media_type") or "video")
    suffix = str(media.get("format") or ("mp4" if media_type == "video" else "jpg"))
    target = directory / f"source-{media.get('_id', 'media')}.{suffix.lstrip('.')}"
    provider = str(media.get("storage_provider") or "cloudinary").lower()
    try:
        if provider == "r2" and media.get("storage_key"):
            download_object(str(media["storage_key"]), target)
            return target, False
        downloaded, _ = download_public_media(str(media.get("secure_url") or ""), media_type)
        return downloaded, True
    except R2MediaError as exc:
        raise AutopilotError(str(exc)) from exc


def _scaled_image(source: Path, target: Path) -> None:
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        "scale='min(720,iw)':-2",
        "-q:v",
        "6",
        "-y",
        str(target),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=90)


def _video_frames(source: Path, directory: Path, count: int, prefix: str) -> list[Path]:
    try:
        _, duration = imageio_ffmpeg.count_frames_and_secs(str(source))
    except Exception:
        duration = 15.0
    duration = max(1.0, float(duration or 15.0))
    output = directory / f"{prefix}-%02d.jpg"
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vf",
        f"fps={count / duration:.8f},scale='min(720,iw)':-2",
        "-frames:v",
        str(count),
        "-q:v",
        "6",
        "-y",
        str(output),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=180)
    return sorted(directory.glob(f"{prefix}-*.jpg"))[:count]


def extract_autopilot_frames(media_documents: list[dict[str, Any]]) -> list[bytes]:
    if not media_documents:
        raise AutopilotError("Aucun média durable à analyser.")
    frame_limit = settings.autopilot_frame_count
    encoded_frames: list[bytes] = []
    external_downloads: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix="igstudio-autopilot-") as temp_name:
            directory = Path(temp_name)
            remaining = frame_limit
            for index, media in enumerate(media_documents[:frame_limit]):
                if remaining <= 0:
                    break
                source, external = _download_stored_media(media, directory)
                if external:
                    external_downloads.append(source)
                documents_left = max(1, min(len(media_documents), frame_limit) - index)
                allocation = max(1, remaining // documents_left)
                try:
                    if str(media.get("media_type")) == "image":
                        target = directory / f"image-{index}.jpg"
                        _scaled_image(source, target)
                        frames = [target]
                    else:
                        frames = _video_frames(source, directory, allocation, f"video-{index}")
                except (OSError, subprocess.SubprocessError) as exc:
                    raise AutopilotError(
                        "Impossible d’extraire les images représentatives du média."
                    ) from exc
                for frame in frames[:remaining]:
                    if frame.is_file() and frame.stat().st_size:
                        encoded_frames.append(frame.read_bytes())
                        remaining -= 1
            if not encoded_frames:
                raise AutopilotError("Aucune image exploitable n’a pu être extraite.")
            return encoded_frames
    finally:
        for path in external_downloads:
            path.unlink(missing_ok=True)


def _normalize_visual_analysis(raw: dict[str, Any]) -> dict[str, Any]:
    def text_value(key: str, limit: int = 600) -> str:
        return str(raw.get(key) or "").strip()[:limit]

    def list_value(key: str, maximum: int = 8) -> list[str]:
        values = raw.get(key) or []
        if not isinstance(values, list):
            values = [values]
        return [str(value).strip()[:240] for value in values[:maximum] if str(value).strip()]

    try:
        score = min(100, max(0, int(raw.get("visual_score") or 0)))
    except (TypeError, ValueError):
        score = 0
    return {
        "summary": text_value("summary"),
        "scenes": list_value("scenes"),
        "visual_style": text_value("visual_style", 300),
        "camera_movement": text_value("camera_movement", 300),
        "apparent_device": text_value("apparent_device", 200),
        "device_consistency": text_value("device_consistency", 400),
        "strengths": list_value("strengths", 6),
        "cautions": list_value("cautions", 6),
        "hook_moment": text_value("hook_moment", 400),
        "objective": text_value("objective", 200),
        "tags": list_value("tags", 10),
        "visual_score": score,
    }


async def analyze_autopilot_media(
    media_documents: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    if not settings.cerebras_api_key:
        raise AutopilotError("CEREBRAS_API_KEY n’est pas configurée.")
    if not settings.cerebras_vision_model:
        raise AutopilotError("CEREBRAS_VISION_MODEL n’est pas configuré.")

    frames = await asyncio.to_thread(
        extract_autopilot_frames,
        media_documents,
    )
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Analyse ces images extraites d’un contenu Instagram. Elles sont ordonnées "
                "dans le temps. Retourne uniquement un objet JSON avec les clés summary, scenes, "
                "visual_style, camera_movement, apparent_device, device_consistency, strengths, "
                "cautions, hook_moment, objective, tags et visual_score. visual_score est un entier "
                "de 0 à 100. N’invente ni lieu ni appareil. L’appareil déclaré est une contrainte "
                "physique : un iPhone seul ne vole pas et ne produit pas automatiquement une vue "
                "aérienne. Évalue le potentiel de différenciation sans promettre de viralité.\n\n"
                f"Titre : {str(context.get('title') or '')[:200]}\n"
                f"Description : {str(context.get('description') or '')[:600]}\n"
                f"Lieu déclaré : {str(context.get('location') or 'non fourni')[:200]}\n"
                f"Appareil déclaré : {str(context.get('device') or 'non fourni')[:200]}\n"
                f"Format : {str(context.get('media_kind') or 'reel')[:40]}"
            ),
        }
    ]
    for frame in frames:
        encoded = base64.b64encode(frame).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            }
        )
    payload = {
        "model": settings.cerebras_vision_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Tu es un directeur éditorial Instagram prudent spécialisé en photo, vidéo, "
                    "drone et FPV. Décris seulement ce qui est visible et retourne du JSON valide."
                ),
            },
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
        "max_completion_tokens": 1600,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.cerebras_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            f"{settings.cerebras_base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
    if response.status_code >= 400:
        raise AutopilotError(str(_groq_error(response)))
    try:
        raw = _extract_json(response.json()["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AutopilotError("Réponse inattendue du modèle visuel Groq.") from exc
    return _normalize_visual_analysis(raw)


WEEKDAY_INDEX = {
    "lundi": 0,
    "mardi": 1,
    "mercredi": 2,
    "jeudi": 3,
    "vendredi": 4,
    "samedi": 5,
    "dimanche": 6,
}


def autopilot_candidate_slots(
    *,
    best_times: list[dict[str, Any]],
    existing_dates: list[datetime],
    posts_per_week: int,
    timezone_name: str,
    item_count: int,
    now: datetime | None = None,
) -> list[str]:
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_timezone = ZoneInfo("Europe/Paris")
    now_utc = (now or utc_now()).astimezone(timezone.utc)
    earliest = now_utc + timedelta(hours=6)
    patterns: list[tuple[int, int]] = []
    for item in best_times:
        weekday = WEEKDAY_INDEX.get(str(item.get("weekday") or "").lower())
        try:
            hour = int(item.get("hour"))
        except (TypeError, ValueError):
            continue
        if weekday is not None and 0 <= hour <= 23 and (weekday, hour) not in patterns:
            patterns.append((weekday, hour))
    for fallback in (
        (1, 18),
        (3, 18),
        (6, 18),
        (4, 19),
        (0, 18),
        (2, 18),
        (5, 18),
    ):
        if fallback not in patterns:
            patterns.append(fallback)
    patterns = patterns[: max(posts_per_week, 3)]

    existing = [value.astimezone(timezone.utc) for value in existing_dates]
    weekly_counts: dict[tuple[int, int], int] = {}
    for value in existing:
        if value >= now_utc:
            week = value.astimezone(local_timezone).isocalendar()[:2]
            weekly_counts[week] = weekly_counts.get(week, 0) + 1
    candidates: list[datetime] = []
    day = earliest.astimezone(local_timezone).replace(hour=0, minute=0, second=0, microsecond=0)
    target_count = max(item_count * 3, item_count + 6)
    for offset in range(120):
        current_day = day + timedelta(days=offset)
        for weekday, hour in patterns:
            if current_day.weekday() != weekday:
                continue
            candidate = current_day.replace(hour=hour).astimezone(timezone.utc)
            if candidate <= earliest:
                continue
            week = candidate.astimezone(local_timezone).isocalendar()[:2]
            if weekly_counts.get(week, 0) >= posts_per_week:
                continue
            if any(abs((candidate - value).total_seconds()) < 3 * 3600 for value in existing):
                continue
            if any(abs((candidate - value).total_seconds()) < 3 * 3600 for value in candidates):
                continue
            candidates.append(candidate)
            weekly_counts[week] = weekly_counts.get(week, 0) + 1
            if len(candidates) >= target_count:
                return [value.isoformat() for value in candidates]
    return [value.isoformat() for value in candidates]


async def generate_autopilot_plan(
    *,
    queue_items: list[dict[str, Any]],
    dashboard: dict[str, Any],
    existing_dates: list[datetime],
    posts_per_week: int,
    timezone_name: str,
) -> dict[str, Any]:
    if not settings.cerebras_api_key:
        raise AutopilotError("CEREBRAS_API_KEY n’est pas configurée.")
    if not queue_items:
        raise AutopilotError("La file d’attente est vide.")
    candidates = autopilot_candidate_slots(
        best_times=list(dashboard.get("best_times") or []),
        existing_dates=existing_dates,
        posts_per_week=posts_per_week,
        timezone_name=timezone_name,
        item_count=len(queue_items),
    )
    if len(candidates) < len(queue_items):
        raise AutopilotError("Impossible de trouver assez de créneaux libres.")
    compact_items = [
        {
            "queue_id": str(item.get("_id") or item.get("id") or ""),
            "title": str(item.get("title") or "")[:160],
            "media_kind": str(item.get("media_kind") or "reel"),
            "location": str(item.get("location") or "")[:120],
            "device": str(item.get("device") or "")[:120],
            "visual_analysis": item.get("visual_analysis") or {},
            "workflow": str(item.get("workflow") or "auto_publish"),
        }
        for item in queue_items
    ]
    system_prompt = """Tu organises une file de contenus Instagram pour maximiser la variété et apprendre des statistiques passées.
Retourne uniquement le JSON demandé. Utilise chaque queue_id exactement une fois et uniquement les scheduled_for fournis dans la liste de créneaux autorisés. Ne programme jamais deux contenus au même instant. Évite d’enchaîner des vidéos visuellement similaires. Les statistiques avec un petit échantillon sont des indices, pas des certitudes. Une publication avec musique peut recevoir un créneau mais nécessitera une finalisation manuelle. La raison doit être courte et concrète."""
    user_prompt = (
        f"Fuseau du créateur : {timezone_name}\n"
        f"Fréquence maximale : {posts_per_week} publication(s) par semaine\n"
        "Créneaux autorisés en UTC :\n"
        + json.dumps(candidates, ensure_ascii=False)
        + "\nStatistiques utiles :\n"
        + json.dumps(
            {
                "summary": dashboard.get("summary") or {},
                "best_times": (dashboard.get("best_times") or [])[:8],
                "automatic_findings": (dashboard.get("automatic_findings") or [])[:8],
            },
            ensure_ascii=False,
        )
        + "\nContenus à répartir :\n"
        + json.dumps(compact_items, ensure_ascii=False)
    )
    payload = {
        "model": settings.cerebras_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_completion_tokens": 4096,
        "response_format": _response_format("autopilot_plan", AUTOPILOT_PLAN_SCHEMA),
        **_reasoning_options(),
    }
    headers = {
        "Authorization": f"Bearer {settings.cerebras_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{settings.cerebras_base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
    if response.status_code >= 400:
        raise AutopilotError(str(_groq_error(response)))
    try:
        raw = _extract_json(response.json()["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AutopilotError("Réponse inattendue du planificateur Groq.") from exc

    allowed_ids = [str(item["queue_id"]) for item in compact_items]
    unused_slots = list(candidates)
    normalized: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        queue_id = str(item.get("queue_id") or "")
        scheduled_for = str(item.get("scheduled_for") or "")
        if queue_id not in allowed_ids or queue_id in by_id or scheduled_for not in unused_slots:
            continue
        unused_slots.remove(scheduled_for)
        try:
            confidence = min(100, max(0, int(item.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0
        by_id[queue_id] = {
            "queue_id": queue_id,
            "scheduled_for": scheduled_for,
            "reason": str(item.get("reason") or "Créneau proposé d’après tes statistiques.")[:600],
            "confidence": confidence,
        }
    for queue_id in allowed_ids:
        proposal = by_id.get(queue_id)
        if proposal is None:
            proposal = {
                "queue_id": queue_id,
                "scheduled_for": unused_slots.pop(0),
                "reason": "Créneau libre retenu d’après les performances historiques.",
                "confidence": 50,
            }
        normalized.append(proposal)
    return {
        "summary": str(raw.get("summary") or "Planning proposé à partir de la file et des statistiques.")[:1200],
        "items": normalized,
        "candidate_count": len(candidates),
    }
