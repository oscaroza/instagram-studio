from pathlib import Path
from typing import Any

import cloudinary
import cloudinary.api
import cloudinary.exceptions
import cloudinary.uploader

from app.config import settings


class CloudinaryMediaError(RuntimeError):
    pass


def safe_cloudinary_failure(exc: Exception, action: str = "envoi") -> str:
    detail = str(exc).lower()
    if "invalid signature" in detail:
        return "Cloudinary refuse la signature : CLOUDINARY_API_SECRET est incorrect."
    if "unknown api key" in detail or "invalid api key" in detail:
        return "Cloudinary refuse la clé : CLOUDINARY_API_KEY est incorrecte."
    if "cloud name" in detail or "cloud_name" in detail:
        return "CLOUDINARY_CLOUD_NAME est incorrect."
    if "file size" in detail or "too large" in detail or "maximum" in detail:
        return (
            "La vidéo dépasse la limite autorisée par ton compte Cloudinary. "
            "Vérifie Account Settings → Usage Limits."
        )
    if "quota" in detail or "usage limit" in detail or "rate limit" in detail:
        return "Le quota ou la limite d’utilisation Cloudinary est atteint."
    if "timeout" in detail or "timed out" in detail:
        return "Cloudinary n’a pas répondu à temps. Réessaie dans un instant."
    if isinstance(exc, cloudinary.exceptions.AuthorizationRequired):
        return (
            "Cloudinary refuse l’authentification. Vérifie le Cloud name, "
            "l’API Key et l’API Secret dans Render."
        )
    if isinstance(exc, cloudinary.exceptions.RateLimited):
        return "La limite de requêtes Cloudinary est atteinte. Réessaie plus tard."
    return (
        f"Cloudinary a refusé l’{action}. Vérifie les trois variables Cloudinary "
        "dans Render et la limite vidéo de ton compte."
    )


def cloudinary_configured() -> bool:
    return bool(
        settings.cloudinary_cloud_name
        and settings.cloudinary_api_key
        and settings.cloudinary_api_secret
    )


def configure_cloudinary() -> None:
    if not cloudinary_configured():
        raise CloudinaryMediaError(
            "Cloudinary n’est pas configuré dans Render."
        )
    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )


def upload_video(path: Path, original_filename: str) -> dict[str, Any]:
    configure_cloudinary()
    try:
        result = cloudinary.uploader.upload_large(
            str(path),
            resource_type="video",
            folder=settings.cloudinary_folder,
            use_filename=True,
            unique_filename=True,
            overwrite=False,
        )
    except Exception as exc:
        raise CloudinaryMediaError(safe_cloudinary_failure(exc)) from exc

    public_id = str(result.get("public_id", ""))
    secure_url = str(result.get("secure_url", ""))
    if not public_id or not secure_url:
        raise CloudinaryMediaError(
            "Cloudinary n’a pas renvoyé les informations du média."
        )

    thumbnail_url = cloudinary.CloudinaryVideo(public_id).build_url(
        format="jpg",
        transformation=[
            {"start_offset": "0"},
            {"width": 640, "height": 360, "crop": "fill", "quality": "auto"},
        ],
        secure=True,
    )

    return {
        "public_id": public_id,
        "secure_url": secure_url,
        "thumbnail_url": thumbnail_url,
        "bytes": int(result.get("bytes", 0) or 0),
        "duration": float(result.get("duration", 0) or 0),
        "format": str(result.get("format", path.suffix.lstrip("."))),
        "width": int(result.get("width", 0) or 0),
        "height": int(result.get("height", 0) or 0),
        "original_filename": original_filename,
    }


def upload_video_url(video_url: str) -> dict[str, Any]:
    if not video_url.startswith(("https://", "http://")):
        raise CloudinaryMediaError("L’URL vidéo à importer est invalide.")
    configure_cloudinary()
    try:
        result = cloudinary.uploader.upload(
            video_url,
            resource_type="video",
            folder=settings.cloudinary_folder,
            unique_filename=True,
            overwrite=False,
        )
    except Exception as exc:
        raise CloudinaryMediaError(
            safe_cloudinary_failure(exc, "import")
        ) from exc

    public_id = str(result.get("public_id", ""))
    secure_url = str(result.get("secure_url", ""))
    if not public_id or not secure_url:
        raise CloudinaryMediaError(
            "Cloudinary n’a pas renvoyé les informations du média."
        )
    thumbnail_url = cloudinary.CloudinaryVideo(public_id).build_url(
        format="jpg",
        transformation=[
            {"start_offset": "0"},
            {"width": 640, "height": 360, "crop": "fill", "quality": "auto"},
        ],
        secure=True,
    )
    parsed_name = Path(video_url.split("?", 1)[0]).name or "video"
    return {
        "public_id": public_id,
        "secure_url": secure_url,
        "thumbnail_url": thumbnail_url,
        "bytes": int(result.get("bytes", 0) or 0),
        "duration": float(result.get("duration", 0) or 0),
        "format": str(result.get("format", "mp4")),
        "width": int(result.get("width", 0) or 0),
        "height": int(result.get("height", 0) or 0),
        "original_filename": parsed_name,
    }


def muted_video_url(public_id: str, video_format: str = "mp4") -> str:
    configure_cloudinary()
    return cloudinary.CloudinaryVideo(public_id).build_url(
        format=video_format or "mp4",
        transformation=[{"audio_codec": "none"}],
        secure=True,
    )


def delete_video(public_id: str) -> None:
    configure_cloudinary()
    try:
        result = cloudinary.uploader.destroy(
            public_id,
            resource_type="video",
            invalidate=True,
        )
    except Exception as exc:
        raise CloudinaryMediaError(
            "La suppression Cloudinary a échoué."
        ) from exc

    if result.get("result") not in {"ok", "not found"}:
        raise CloudinaryMediaError(
            "Cloudinary a refusé la suppression du média."
        )
