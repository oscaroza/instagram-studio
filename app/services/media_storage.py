import tempfile
from pathlib import Path

from app.config import settings
from app.services.cloudinary_media import (
    CloudinaryMediaError,
    cloudinary_configured,
    delete_media as delete_cloudinary_media,
    muted_video_url,
    upload_image,
    upload_image_url,
    upload_video,
    upload_video_url,
    verify_cloudinary_connection,
)
from app.services.local_media import LocalMediaError, mute_video_path
from app.services.r2_media import (
    R2MediaError,
    delete_objects,
    download_object,
    download_public_media,
    r2_configured,
    upload_media_path as upload_r2_path,
    upload_media_url as upload_r2_url,
    verify_r2_connection,
)


class MediaStorageError(RuntimeError):
    pass


def active_storage_provider() -> str:
    requested = settings.media_storage_backend
    if requested == "r2":
        return "r2"
    if requested == "cloudinary":
        return "cloudinary"
    if r2_configured():
        return "r2"
    if cloudinary_configured():
        return "cloudinary"
    return "r2"


def media_storage_configured() -> bool:
    provider = active_storage_provider()
    return r2_configured() if provider == "r2" else cloudinary_configured()


def storage_provider_label(provider: str | None = None) -> str:
    return "Cloudflare R2" if (provider or active_storage_provider()) == "r2" else "Cloudinary"


def verify_active_storage() -> dict:
    provider = active_storage_provider()
    if provider == "r2":
        if not r2_configured():
            raise MediaStorageError("Les variables Cloudflare R2 sont incomplètes.")
        try:
            status = verify_r2_connection()
        except R2MediaError as exc:
            raise MediaStorageError(str(exc)) from exc
        return {"provider": provider, **status}
    if not cloudinary_configured():
        raise MediaStorageError("Les variables Cloudinary sont incomplètes.")
    try:
        ready = verify_cloudinary_connection()
    except CloudinaryMediaError as exc:
        raise MediaStorageError(str(exc)) from exc
    return {"provider": provider, "ready": ready, "usage_bytes": 0, "limit_bytes": 0}


def _cloudinary_result(result: dict, media_type: str, mute_audio: bool) -> dict:
    result = dict(result)
    result["storage_provider"] = "cloudinary"
    result["storage_key"] = result["public_id"]
    result["publication_url"] = (
        muted_video_url(result["public_id"], result.get("format", "mp4"))
        if media_type == "video" and mute_audio
        else result["secure_url"]
    )
    result["muted"] = False
    return result


def store_media_path(
    path: Path,
    original_filename: str,
    media_type: str,
    mute_audio: bool = False,
) -> dict:
    provider = active_storage_provider()
    muted_path = None
    try:
        if provider == "r2":
            source = path
            if media_type == "video" and mute_audio:
                source = muted_path = mute_video_path(path)
                original_filename = f"{Path(original_filename).stem}-muted.mp4"
            result = upload_r2_path(source, original_filename, media_type)
            result["publication_url"] = result["secure_url"]
            result["muted"] = bool(media_type == "video" and mute_audio)
            return result

        upload_function = upload_image if media_type == "image" else upload_video
        return _cloudinary_result(
            upload_function(path, original_filename), media_type, mute_audio
        )
    except (CloudinaryMediaError, R2MediaError, LocalMediaError) as exc:
        raise MediaStorageError(str(exc)) from exc
    finally:
        if muted_path:
            muted_path.unlink(missing_ok=True)


def store_media_url(media_url: str, media_type: str, mute_audio: bool = False) -> dict:
    provider = active_storage_provider()
    downloaded_path = None
    muted_path = None
    try:
        if provider == "r2" and media_type == "video" and mute_audio:
            downloaded_path, original_name = download_public_media(media_url, media_type)
            muted_path = mute_video_path(downloaded_path)
            result = upload_r2_path(
                muted_path,
                f"{Path(original_name).stem}-muted.mp4",
                media_type,
            )
            result["publication_url"] = result["secure_url"]
            result["muted"] = True
            return result
        if provider == "r2":
            result = upload_r2_url(media_url, media_type)
            result["publication_url"] = result["secure_url"]
            result["muted"] = False
            return result

        upload_function = upload_image_url if media_type == "image" else upload_video_url
        return _cloudinary_result(upload_function(media_url), media_type, mute_audio)
    except (CloudinaryMediaError, R2MediaError, LocalMediaError) as exc:
        raise MediaStorageError(str(exc)) from exc
    finally:
        if downloaded_path:
            downloaded_path.unlink(missing_ok=True)
        if muted_path:
            muted_path.unlink(missing_ok=True)


def stored_media_provider(media: dict) -> str:
    return str(media.get("storage_provider") or "cloudinary").lower()


def delete_stored_media(media: dict) -> None:
    provider = stored_media_provider(media)
    try:
        if provider == "r2":
            delete_objects(
                [
                    str(media.get("storage_key", "")),
                    str(media.get("thumbnail_storage_key", "")),
                    str(media.get("muted_storage_key", "")),
                ]
            )
            return
        public_id = str(media.get("cloudinary_public_id") or media.get("storage_key") or "")
        if public_id:
            delete_cloudinary_media(
                public_id,
                str(media.get("resource_type") or media.get("media_type") or "video"),
            )
    except (CloudinaryMediaError, R2MediaError) as exc:
        raise MediaStorageError(str(exc)) from exc


def prepare_muted_media(media: dict) -> dict:
    provider = stored_media_provider(media)
    if provider == "cloudinary":
        if not cloudinary_configured():
            raise MediaStorageError(
                "Cloudinary doit rester configuré pour utiliser cet ancien média."
            )
        public_id = str(media.get("cloudinary_public_id") or media.get("storage_key") or "")
        return {
            "url": muted_video_url(public_id, str(media.get("format") or "mp4")),
            "updates": {},
        }
    if not r2_configured():
        raise MediaStorageError("Cloudflare R2 n’est pas configuré.")
    if media.get("muted"):
        return {"url": str(media.get("secure_url", "")), "updates": {}}
    if media.get("muted_url") and media.get("muted_storage_key"):
        return {"url": str(media["muted_url"]), "updates": {}}

    source_handle = tempfile.NamedTemporaryFile(suffix=f".{media.get('format', 'mp4')}", delete=False)
    source = Path(source_handle.name)
    source_handle.close()
    muted = None
    try:
        download_object(str(media.get("storage_key", "")), source)
        muted = mute_video_path(source)
        result = upload_r2_path(
            muted,
            f"{Path(str(media.get('original_filename') or 'video')).stem}-muted.mp4",
            "video",
        )
        updates = {
            "muted_url": result["secure_url"],
            "muted_storage_key": result["storage_key"],
            "muted_bytes": result["bytes"],
        }
        return {"url": result["secure_url"], "updates": updates}
    except (R2MediaError, LocalMediaError) as exc:
        raise MediaStorageError(str(exc)) from exc
    finally:
        source.unlink(missing_ok=True)
        if muted:
            muted.unlink(missing_ok=True)
