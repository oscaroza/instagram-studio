import ipaddress
import mimetypes
import re
import socket
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import boto3
import httpx
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings


class R2MediaError(RuntimeError):
    pass


MULTIPART_CONFIG = TransferConfig(
    multipart_threshold=16 * 1024 * 1024,
    multipart_chunksize=16 * 1024 * 1024,
    max_concurrency=4,
    use_threads=True,
)


def r2_configured() -> bool:
    return bool(
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and settings.r2_bucket_name
        and settings.r2_public_base_url.startswith("https://")
    )


def r2_limit_bytes() -> int:
    return int(settings.r2_max_storage_gb * 1_000_000_000)


def safe_r2_failure(exc: Exception, action: str = "envoi") -> str:
    code = ""
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", ""))
    normalized = code.lower()
    if normalized in {"invalidaccesskeyid", "signaturedoesnotmatch"}:
        return "R2 refuse les identifiants. Vérifie l’Access Key ID et la Secret Access Key."
    if normalized in {"accessdenied", "unauthorized"}:
        return "R2 refuse l’accès. Le token doit avoir Object Read & Write sur ce bucket."
    if normalized in {"nosuchbucket", "404"}:
        return "Le bucket R2 indiqué dans R2_BUCKET_NAME est introuvable."
    if isinstance(exc, (BotoCoreError, ClientError)):
        return f"R2 a refusé l’{action} ({code or type(exc).__name__})."
    safe_detail = str(exc)
    for value in (
        settings.r2_secret_access_key,
        settings.r2_access_key_id,
        settings.r2_account_id,
    ):
        if value:
            safe_detail = safe_detail.replace(value, "[valeur masquée]")
    safe_detail = re.sub(r"\b[a-fA-F0-9]{32,64}\b", "[valeur masquée]", safe_detail)
    return f"R2 a refusé l’{action} ({type(exc).__name__}) : {' '.join(safe_detail.split())[:240]}"


def r2_client():
    if not r2_configured():
        raise R2MediaError("Cloudflare R2 n’est pas complètement configuré dans Render.")
    return boto3.client(
        service_name="s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )


def bucket_usage_bytes(client=None) -> int:
    active_client = client or r2_client()
    total = 0
    continuation_token = None
    while True:
        parameters = {"Bucket": settings.r2_bucket_name, "MaxKeys": 1000}
        if continuation_token:
            parameters["ContinuationToken"] = continuation_token
        response = active_client.list_objects_v2(**parameters)
        total += sum(int(item.get("Size", 0)) for item in response.get("Contents", []))
        if not response.get("IsTruncated"):
            return total
        continuation_token = response.get("NextContinuationToken")


def verify_r2_connection() -> dict[str, int | bool]:
    try:
        usage = bucket_usage_bytes()
    except Exception as exc:
        raise R2MediaError(safe_r2_failure(exc, "authentification")) from exc
    return {"ready": True, "usage_bytes": usage, "limit_bytes": r2_limit_bytes()}


def _ensure_free_capacity(incoming_bytes: int, client) -> None:
    usage = bucket_usage_bytes(client)
    if usage + incoming_bytes > r2_limit_bytes():
        remaining = max(0, r2_limit_bytes() - usage)
        raise R2MediaError(
            "Envoi bloqué pour rester dans l’offre gratuite R2 : "
            f"il reste environ {remaining / 1_000_000_000:.2f} Go sur la limite Studio de "
            f"{settings.r2_max_storage_gb:g} Go. Supprime d’abord des médias de la bibliothèque."
        )


def _safe_name(filename: str, media_type: str) -> tuple[str, str]:
    raw = Path(filename or ("video.mp4" if media_type == "video" else "photo.jpg"))
    suffix = raw.suffix.lower()
    allowed = {".mp4", ".mov", ".m4v"} if media_type == "video" else {".jpg", ".jpeg"}
    if suffix not in allowed:
        suffix = ".mp4" if media_type == "video" else ".jpg"
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw.stem).strip("-")[:60] or media_type
    return stem, suffix


def _object_key(filename: str, media_type: str) -> str:
    stem, suffix = _safe_name(filename, media_type)
    date_path = datetime.now(timezone.utc).strftime("%Y/%m")
    prefix = f"{settings.r2_folder}/" if settings.r2_folder else ""
    return f"{prefix}{date_path}/{uuid.uuid4().hex}-{stem}{suffix}"


def public_object_url(object_key: str) -> str:
    return f"{settings.r2_public_base_url}/{quote(object_key, safe='/')}"


def _verify_public_access(public_url: str) -> None:
    try:
        response = httpx.head(public_url, follow_redirects=True, timeout=20.0)
    except httpx.HTTPError as exc:
        raise R2MediaError(
            "Le fichier est stocké dans R2, mais son domaine public ne répond pas. "
            "Vérifie R2_PUBLIC_BASE_URL et le domaine public du bucket."
        ) from exc
    if response.status_code < 200 or response.status_code >= 400:
        raise R2MediaError(
            "Le fichier est stocké dans R2, mais il n’est pas accessible publiquement. "
            "Active le domaine public du bucket et vérifie R2_PUBLIC_BASE_URL."
        )


def _validate_media_file(path: Path, media_type: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise R2MediaError("Le fichier à envoyer vers R2 est vide ou introuvable.")
    if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
        raise R2MediaError(f"Le média dépasse la limite Studio de {settings.max_upload_mb} Mo.")
    if media_type == "image":
        with path.open("rb") as handle:
            if handle.read(3) != b"\xff\xd8\xff":
                raise R2MediaError("Instagram accepte uniquement les photos JPEG valides.")


def upload_media_path(path: Path, original_filename: str, media_type: str) -> dict:
    if media_type not in {"image", "video"}:
        raise R2MediaError("Type de média R2 invalide.")
    _validate_media_file(path, media_type)
    client = r2_client()
    size = path.stat().st_size
    object_key = _object_key(original_filename, media_type)
    content_type = mimetypes.guess_type(object_key)[0] or (
        "video/mp4" if media_type == "video" else "image/jpeg"
    )
    try:
        _ensure_free_capacity(size, client)
        client.upload_file(
            str(path),
            settings.r2_bucket_name,
            object_key,
            ExtraArgs={
                "ContentType": content_type,
                "CacheControl": "public, max-age=31536000, immutable",
                "ContentDisposition": "inline",
            },
            Config=MULTIPART_CONFIG,
        )
    except R2MediaError:
        raise
    except Exception as exc:
        raise R2MediaError(safe_r2_failure(exc)) from exc

    public_url = public_object_url(object_key)
    try:
        _verify_public_access(public_url)
    except R2MediaError:
        try:
            client.delete_object(Bucket=settings.r2_bucket_name, Key=object_key)
        except Exception:
            pass
        raise
    _, suffix = _safe_name(original_filename, media_type)
    return {
        "storage_provider": "r2",
        "storage_key": object_key,
        "secure_url": public_url,
        "thumbnail_url": public_url if media_type == "image" else "",
        "bytes": size,
        "duration": 0.0,
        "format": suffix.lstrip("."),
        "width": 0,
        "height": 0,
        "original_filename": original_filename,
        "media_type": media_type,
        "resource_type": media_type,
    }


def _validate_public_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise R2MediaError("L’URL du média à importer est invalide.")
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise R2MediaError("Le domaine du média est introuvable.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise R2MediaError("L’import depuis une adresse privée ou locale est refusé.")


def download_public_media(media_url: str, media_type: str) -> tuple[Path, str]:
    current_url = media_url
    maximum = settings.max_upload_mb * 1024 * 1024
    suffix = _safe_name(Path(urlparse(media_url).path).name, media_type)[1]
    temporary = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    target = Path(temporary.name)
    temporary.close()
    try:
        with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
            for _ in range(4):
                _validate_public_url(current_url)
                with client.stream("GET", current_url, follow_redirects=False) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            raise R2MediaError("La redirection du média est invalide.")
                        current_url = urljoin(current_url, location)
                        continue
                    response.raise_for_status()
                    announced = int(response.headers.get("content-length", "0") or 0)
                    if announced > maximum:
                        raise R2MediaError(f"Le média dépasse la limite Studio de {settings.max_upload_mb} Mo.")
                    written = 0
                    with target.open("wb") as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            written += len(chunk)
                            if written > maximum:
                                raise R2MediaError(f"Le média dépasse la limite Studio de {settings.max_upload_mb} Mo.")
                            output.write(chunk)
                    original_name = Path(urlparse(current_url).path).name or target.name
                    _validate_media_file(target, media_type)
                    return target, original_name
            raise R2MediaError("Le média effectue trop de redirections.")
    except R2MediaError:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise R2MediaError(safe_r2_failure(exc, "import")) from exc


def upload_media_url(media_url: str, media_type: str) -> dict:
    target, original_name = download_public_media(media_url, media_type)
    try:
        return upload_media_path(target, original_name, media_type)
    finally:
        target.unlink(missing_ok=True)


def download_object(object_key: str, target: Path) -> None:
    try:
        r2_client().download_file(settings.r2_bucket_name, object_key, str(target))
    except Exception as exc:
        raise R2MediaError(safe_r2_failure(exc, "téléchargement")) from exc


def delete_objects(object_keys: list[str]) -> None:
    keys = list(dict.fromkeys(key for key in object_keys if key))
    if not keys:
        return
    try:
        client = r2_client()
        for start in range(0, len(keys), 1000):
            response = client.delete_objects(
                Bucket=settings.r2_bucket_name,
                Delete={"Objects": [{"Key": key} for key in keys[start : start + 1000]]},
            )
            if response.get("Errors"):
                raise R2MediaError("R2 a refusé la suppression d’un ou plusieurs objets.")
    except R2MediaError:
        raise
    except Exception as exc:
        raise R2MediaError(safe_r2_failure(exc, "suppression")) from exc
