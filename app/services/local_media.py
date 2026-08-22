import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg

from app.config import settings


UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads"


class LocalMediaError(RuntimeError):
    pass


def local_media_path(video_url: str) -> Path | None:
    parsed = urlparse(video_url)
    expected = urlparse(settings.app_base_url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
        return None
    if not parsed.path.startswith("/media/"):
        return None

    filename = parsed.path.removeprefix("/media/")
    if not filename or Path(filename).name != filename:
        return None
    path = UPLOAD_DIR / filename
    return path if path.is_file() else None


def mute_local_video(video_url: str) -> dict[str, str]:
    source = local_media_path(video_url)
    if source is None:
        raise LocalMediaError(
            "Pour couper le son sans Cloudinary, importe d’abord la vidéo dans le Studio."
        )

    target_name = f"{uuid.uuid4().hex}-muted.mp4"
    target = UPLOAD_DIR / target_name
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-an",
        "-movflags",
        "+faststart",
        "-y",
        str(target),
    ]
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        target.unlink(missing_ok=True)
        raise LocalMediaError("Impossible de créer la version sans son.") from exc

    if not target.is_file() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise LocalMediaError("La version sans son est invalide.")
    return {
        "url": f"{settings.app_base_url}/media/{target_name}",
        "filename": target_name,
    }
