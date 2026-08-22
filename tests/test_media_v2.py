import asyncio
import subprocess

import imageio_ffmpeg
import cloudinary.exceptions

from app.config import settings
from app.services import local_media, token_store
from app.services.cloudinary_media import muted_video_url, safe_cloudinary_failure


def test_mute_local_video_removes_audio_track(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-shortest",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-y",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(local_media, "UPLOAD_DIR", tmp_path)
    previous_base = settings.app_base_url
    object.__setattr__(settings, "app_base_url", "https://studio.example")
    try:
        result = local_media.mute_local_video(
            "https://studio.example/media/source.mp4"
        )
    finally:
        object.__setattr__(settings, "app_base_url", previous_base)

    output = tmp_path / result["filename"]
    inspected = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(output)],
        capture_output=True,
        text=True,
    )
    assert output.is_file()
    assert "Video:" in inspected.stderr
    assert "Audio:" not in inspected.stderr


def test_cloudinary_muted_url_uses_documented_audio_codec(monkeypatch):
    values = {
        "cloudinary_cloud_name": "test-cloud",
        "cloudinary_api_key": "test-key",
        "cloudinary_api_secret": "test-secret",
    }
    originals = {key: getattr(settings, key) for key in values}
    try:
        for key, value in values.items():
            object.__setattr__(settings, key, value)
        result = muted_video_url("instagram-studio/video", "mp4")
    finally:
        for key, value in originals.items():
            object.__setattr__(settings, key, value)
    assert "/ac_none/" in result
    assert result.endswith("/instagram-studio/video.mp4")


def test_instagram_credentials_fall_back_when_mongodb_is_down(monkeypatch):
    async def fail_to_thread(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(token_store, "database_configured", lambda: True)
    monkeypatch.setattr(token_store.asyncio, "to_thread", fail_to_thread)
    previous_user = settings.instagram_user_id
    previous_token = settings.instagram_access_token
    object.__setattr__(settings, "instagram_user_id", "fallback-user")
    object.__setattr__(settings, "instagram_access_token", "fallback-token")
    try:
        credentials = asyncio.run(token_store.resolve_instagram_credentials())
        saved = asyncio.run(
            token_store.save_credentials(
                user_id="fallback-user",
                access_token="fallback-token",
                expires_in=3600,
            )
        )
    finally:
        object.__setattr__(settings, "instagram_user_id", previous_user)
        object.__setattr__(settings, "instagram_access_token", previous_token)

    assert credentials == ("fallback-user", "fallback-token")
    assert saved is False


def test_cloudinary_errors_identify_bad_credentials_without_echoing_values():
    secret = "never-echo-this-secret"
    signature_error = safe_cloudinary_failure(
        cloudinary.exceptions.BadRequest(f"Invalid Signature {secret}")
    )
    key_error = safe_cloudinary_failure(
        cloudinary.exceptions.AuthorizationRequired("Unknown API key")
    )

    assert "CLOUDINARY_API_SECRET" in signature_error
    assert "CLOUDINARY_API_KEY" in key_error
    assert secret not in signature_error
