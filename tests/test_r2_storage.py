import asyncio
from contextlib import contextmanager
from pathlib import Path

from app.config import settings
from app.routes import v2
from app.services import media_storage, r2_media


@contextmanager
def temporary_settings(**values):
    originals = {key: getattr(settings, key) for key in values}
    try:
        for key, value in values.items():
            object.__setattr__(settings, key, value)
        yield
    finally:
        for key, value in originals.items():
            object.__setattr__(settings, key, value)


R2_SETTINGS = {
    "media_storage_backend": "r2",
    "r2_account_id": "account-id",
    "r2_access_key_id": "access-key",
    "r2_secret_access_key": "secret-key",
    "r2_bucket_name": "instagram-studio",
    "r2_public_base_url": "https://media.example.com",
    "r2_folder": "instagram-studio",
    "r2_max_storage_gb": 9.0,
}


class FakeR2Client:
    def __init__(self, usage=0):
        self.usage = usage
        self.uploads = []
        self.deleted = []

    def list_objects_v2(self, **parameters):
        contents = [{"Key": "existing", "Size": self.usage}] if self.usage else []
        return {"Contents": contents, "IsTruncated": False}

    def upload_file(self, filename, bucket, key, ExtraArgs, Config):
        self.uploads.append(
            {
                "filename": filename,
                "bucket": bucket,
                "key": key,
                "extra": ExtraArgs,
                "config": Config,
            }
        )

    def delete_object(self, **parameters):
        self.deleted.append(parameters["Key"])

    def delete_objects(self, **parameters):
        self.deleted.extend(item["Key"] for item in parameters["Delete"]["Objects"])
        return {"Deleted": parameters["Delete"]["Objects"]}


def test_r2_upload_uses_multipart_configuration_and_public_url(tmp_path, monkeypatch):
    photo = tmp_path / "plage.jpg"
    photo.write_bytes(b"\xff\xd8\xffphoto")
    client = FakeR2Client()
    monkeypatch.setattr(r2_media, "r2_client", lambda: client)
    monkeypatch.setattr(r2_media, "_verify_public_access", lambda url: None)

    with temporary_settings(**R2_SETTINGS):
        result = r2_media.upload_media_path(photo, "Plage été.jpg", "image")

    assert result["storage_provider"] == "r2"
    assert result["secure_url"].startswith("https://media.example.com/instagram-studio/")
    assert result["thumbnail_url"] == result["secure_url"]
    assert client.uploads[0]["bucket"] == "instagram-studio"
    assert client.uploads[0]["extra"]["ContentType"] == "image/jpeg"
    assert client.uploads[0]["config"].multipart_threshold == 16 * 1024 * 1024


def test_r2_upload_is_blocked_before_free_storage_cap(tmp_path, monkeypatch):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    client = FakeR2Client(usage=499_999_999)
    monkeypatch.setattr(r2_media, "r2_client", lambda: client)
    monkeypatch.setattr(r2_media, "_verify_public_access", lambda url: None)

    with temporary_settings(**{**R2_SETTINGS, "r2_max_storage_gb": 0.5}):
        try:
            r2_media.upload_media_path(photo, "photo.jpg", "image")
        except r2_media.R2MediaError as exc:
            message = str(exc)
        else:
            raise AssertionError("L’envoi aurait dû être bloqué avant le plafond R2")

    assert "offre gratuite" in message
    assert not client.uploads


def test_r2_errors_never_echo_credentials():
    with temporary_settings(**R2_SETTINGS):
        message = r2_media.safe_r2_failure(
            RuntimeError("failure account-id access-key secret-key")
        )

    assert "account-id" not in message
    assert "access-key" not in message
    assert "secret-key" not in message


def test_auto_storage_prefers_r2_and_keeps_legacy_cloudinary(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        media_storage,
        "delete_cloudinary_media",
        lambda public_id, resource_type: deleted.append((public_id, resource_type)),
    )
    with temporary_settings(
        **{
            **R2_SETTINGS,
            "media_storage_backend": "auto",
            "cloudinary_cloud_name": "legacy",
            "cloudinary_api_key": "legacy-key",
            "cloudinary_api_secret": "legacy-secret",
        }
    ):
        assert media_storage.active_storage_provider() == "r2"
        media_storage.delete_stored_media(
            {
                "cloudinary_public_id": "legacy/video",
                "resource_type": "video",
            }
        )

    assert deleted == [("legacy/video", "video")]


def test_r2_muted_variant_is_tracked_for_future_deletion(tmp_path, monkeypatch):
    muted = tmp_path / "muted.mp4"
    muted.write_bytes(b"muted-video")
    monkeypatch.setattr(
        media_storage,
        "download_object",
        lambda object_key, target: target.write_bytes(b"source-video"),
    )
    monkeypatch.setattr(media_storage, "mute_video_path", lambda source: muted)
    monkeypatch.setattr(
        media_storage,
        "upload_r2_path",
        lambda *args: {
            "secure_url": "https://media.example.com/muted.mp4",
            "storage_key": "muted-key",
            "bytes": 11,
        },
    )

    with temporary_settings(**R2_SETTINGS):
        result = media_storage.prepare_muted_media(
            {
                "storage_provider": "r2",
                "storage_key": "original-key",
                "format": "mp4",
                "original_filename": "montage.mp4",
            }
        )

    assert result["url"].endswith("muted.mp4")
    assert result["updates"] == {
        "muted_url": "https://media.example.com/muted.mp4",
        "muted_storage_key": "muted-key",
        "muted_bytes": 11,
    }


def test_promoted_media_records_its_storage_provider(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    inserted = []

    class InsertResult:
        inserted_id = "media-id"

    class Media:
        def insert_one(self, document):
            inserted.append(document.copy())
            return InsertResult()

    class Database:
        media = Media()

    monkeypatch.setattr(v2, "database_configured", lambda: True)
    monkeypatch.setattr(v2, "media_storage_configured", lambda: True)
    monkeypatch.setattr(v2, "local_media_path", lambda url: source)
    monkeypatch.setattr(v2, "database", lambda: Database())
    monkeypatch.setattr(
        v2,
        "store_media_path",
        lambda *args: {
            "storage_provider": "r2",
            "storage_key": "video-key",
            "secure_url": "https://media.example.com/video.mp4",
            "publication_url": "https://media.example.com/video.mp4",
            "thumbnail_url": "",
            "bytes": 5,
            "duration": 0,
            "format": "mp4",
            "width": 0,
            "height": 0,
            "original_filename": "video.mp4",
            "media_type": "video",
            "resource_type": "video",
            "muted": False,
        },
    )

    result = asyncio.run(
        v2.promote_media_to_library(
            {"media_url": "https://studio.example/media/video.mp4", "media_type": "video"}
        )
    )

    assert result["ok"] is True
    assert result["media"]["storage_provider"] == "r2"
    assert inserted[0]["storage_key"] == "video-key"
