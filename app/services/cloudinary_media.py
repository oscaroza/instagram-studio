from pathlib import Path
from typing import Any

import cloudinary
import cloudinary.uploader

from app.config import settings


class CloudinaryMediaError(RuntimeError):
    pass


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
        raise CloudinaryMediaError(
            "L’envoi de la vidéo vers Cloudinary a échoué."
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
