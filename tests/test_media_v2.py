import asyncio
import subprocess
from datetime import timedelta

import imageio_ffmpeg
import cloudinary.exceptions

from app.config import settings
from app.routes import v2
from app.services import local_media, publication_safety, token_store
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


def test_unknown_cloudinary_error_is_safely_redacted():
    values = {
        "cloudinary_cloud_name": "private-cloud",
        "cloudinary_api_key": "private-api-key",
        "cloudinary_api_secret": "private-api-secret",
    }
    originals = {key: getattr(settings, key) for key in values}
    try:
        for key, value in values.items():
            object.__setattr__(settings, key, value)
        result = safe_cloudinary_failure(
            RuntimeError(
                "Unexpected failure private-cloud private-api-key private-api-secret"
            )
        )
    finally:
        for key, value in originals.items():
            object.__setattr__(settings, key, value)

    assert "Unexpected failure" in result
    assert "private-cloud" not in result
    assert "private-api-key" not in result
    assert "private-api-secret" not in result


def test_manual_music_workflow_accepts_photos_and_carousels(monkeypatch):
    inserted = []

    class InsertResult:
        inserted_id = "publication-id"

    class Publications:
        def insert_one(self, document):
            inserted.append(document)
            return InsertResult()

    class Database:
        publications = Publications()

    monkeypatch.setattr(v2, "database_configured", lambda: True)
    monkeypatch.setattr(v2, "database", lambda: Database())

    for media_kind, count in (("photo", 1), ("carousel", 2)):
        payload = {
            "media_kind": media_kind,
            "media_items": [
                {
                    "url": f"https://studio.example/photo-{index}.jpg",
                    "media_type": "image",
                }
                for index in range(count)
            ],
            "workflow": "manual_music",
            "publication_mode": "normal",
        }
        result = asyncio.run(v2.create_publication(payload))

        assert result["ok"] is True
        assert result["publication"]["status"] == "awaiting_manual"
        assert result["publication"]["media_kind"] == media_kind

    assert [document["media_kind"] for document in inserted] == ["photo", "carousel"]


def test_carousel_validation_accepts_videos_and_mixed_media():
    items = v2.publication_media_items(
        {
            "media_items": [
                {
                    "url": "https://studio.example/photo.jpg",
                    "media_type": "image",
                },
                {
                    "url": "https://studio.example/video.mp4",
                    "media_type": "video",
                },
            ]
        },
        "carousel",
    )

    assert [item["media_type"] for item in items] == ["image", "video"]


def test_publication_claim_blocks_duplicate_until_released(monkeypatch):
    monkeypatch.setattr(publication_safety, "database_configured", lambda: False)
    key = publication_safety.publication_fingerprint(
        media_kind="reel",
        media_items=[{"url": "https://studio.example/reel.mp4"}],
        caption="Caption unique",
        publication_mode="normal",
        workflow="auto_publish",
    )

    publication_safety.release_publication_claim(key)
    assert publication_safety.claim_publication(key) is True
    assert publication_safety.claim_publication(key) is False
    publication_safety.release_publication_claim(key)
    assert publication_safety.claim_publication(key) is True
    publication_safety.release_publication_claim(key)


def test_scheduled_publication_can_be_moved_and_reminder_is_reset(monkeypatch):
    calls = []

    class UpdateResult:
        modified_count = 1

    class Publications:
        def update_one(self, query, update):
            calls.append((query, update))
            return UpdateResult()

    class Database:
        publications = Publications()

    monkeypatch.setattr(v2, "database_configured", lambda: True)
    monkeypatch.setattr(v2, "database", lambda: Database())
    monkeypatch.setattr(v2, "object_id", lambda value: f"object-{value}")
    future = v2.utc_now() + timedelta(days=2)

    result = asyncio.run(
        v2.reschedule_publication(
            "publication-id",
            {"scheduled_for": future.isoformat()},
        )
    )

    assert result["ok"] is True
    assert calls[0][0] == {"_id": "object-publication-id", "status": "scheduled"}
    assert calls[0][1]["$set"]["scheduled_for"] == future
    assert "reminder_sent_at" in calls[0][1]["$unset"]


def test_library_reports_media_usage_for_filters(monkeypatch):
    now = v2.utc_now()

    class Cursor(list):
        def sort(self, *args):
            return self

        def limit(self, *args):
            return self

    class Media:
        def find(self, *args):
            return Cursor(
                [
                    {
                        "_id": "media-1",
                        "original_filename": "plage.jpg",
                        "description": "Coucher de soleil",
                        "bytes": 1234,
                        "created_at": now,
                    }
                ]
            )

    class Publications:
        def find(self, *args):
            return Cursor(
                [
                    {
                        "library_ids": ["media-1"],
                        "status": "scheduled",
                        "scheduled_for": now + timedelta(days=1),
                    }
                ]
            )

    class Database:
        media = Media()
        publications = Publications()

    monkeypatch.setattr(v2, "database_configured", lambda: True)
    monkeypatch.setattr(v2, "database", lambda: Database())

    result = asyncio.run(v2.list_library())

    assert result["ok"] is True
    assert result["items"][0]["usage_count"] == 1
    assert result["items"][0]["active_usage_count"] == 1
    assert result["items"][0]["description"] == "Coucher de soleil"
