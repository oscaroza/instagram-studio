import asyncio
from contextlib import contextmanager
from pathlib import Path

from app.config import settings
from app.routes import v2
from app.services import cloudflare_usage, media_storage, r2_media


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


class FakeAnalyticsResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeAnalyticsClient:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    def post(self, url, headers, json):
        self.request = {"url": url, "headers": headers, "json": json}
        return FakeAnalyticsResponse(self.payload)


class FakeBillingClient:
    def __init__(self, usage_payload, subscriptions_payload=None, usage_status=200):
        self.usage_payload = usage_payload
        self.subscriptions_payload = subscriptions_payload or {"success": True, "result": []}
        self.usage_status = usage_status
        self.requests = []

    def get(self, url, headers, params):
        self.requests.append({"url": url, "headers": headers, "params": params})
        if url.endswith("/billable/usage"):
            return FakeAnalyticsResponse(self.usage_payload, self.usage_status)
        return FakeAnalyticsResponse(self.subscriptions_payload)


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


def test_r2_dashboard_classifies_monthly_operations_and_storage():
    payload = {
        "data": {
            "viewer": {
                "accounts": [
                    {
                        "operations": [
                            {"sum": {"requests": 120}, "dimensions": {"actionType": "PutObject"}},
                            {"sum": {"requests": 30}, "dimensions": {"actionType": "ListObjectsV2"}},
                            {"sum": {"requests": 5000}, "dimensions": {"actionType": "GetObject"}},
                            {"sum": {"requests": 8}, "dimensions": {"actionType": "DeleteObject"}},
                            {"sum": {"requests": 2}, "dimensions": {"actionType": "FutureAction"}},
                        ],
                        "storage": [
                            {
                                "max": {
                                    "objectCount": 12,
                                    "payloadSize": 2_400_000_000,
                                    "metadataSize": 2_000,
                                },
                                "dimensions": {"datetime": "2026-08-24T10:00:00Z"},
                            }
                        ],
                    }
                ]
            }
        }
    }
    analytics_client = FakeAnalyticsClient(payload)
    billing_client = FakeBillingClient(
        {
            "success": True,
            "result": [
                {
                    "x_ProductFamilyName": "R2",
                    "x_BillableMetricId": "r2_class_a_operations",
                    "ConsumedQuantity": 150,
                    "BillingPeriodStart": "2026-08-24T00:00:00Z",
                    "BillingPeriodEnd": "2026-09-24T00:00:00Z",
                    "BilledCost": 0,
                    "BillingCurrency": "USD",
                },
                {
                    "x_ProductFamilyName": "R2",
                    "x_BillableMetricName": "R2 Class B Operations",
                    "ConsumedQuantity": 5000,
                    "BillingPeriodStart": "2026-08-24T00:00:00Z",
                    "BillingPeriodEnd": "2026-09-24T00:00:00Z",
                    "BilledCost": 0,
                    "BillingCurrency": "USD",
                },
            ],
        }
    )
    with temporary_settings(
        **R2_SETTINGS,
        cloudflare_analytics_api_token="analytics-secret-token",
        cloudflare_billing_api_token="billing-secret-token",
    ):
        result = cloudflare_usage.r2_usage_summary(
            now=cloudflare_usage.datetime(2026, 8, 24, tzinfo=cloudflare_usage.timezone.utc),
            analytics_client=analytics_client,
            billing_client=billing_client,
            storage_client=FakeR2Client(usage=2_000_000_000),
        )

    assert result["analytics_ready"] is True
    assert result["billing_ready"] is True
    assert result["billing_authoritative"] is True
    assert result["usage_source"] == "cloudflare_billing"
    assert result["class_a"]["used"] == 150
    assert result["class_a"]["remaining"] == 999_850
    assert result["class_b"]["used"] == 5000
    assert result["free_operations"] == 8
    assert result["unknown_operations"] == 2
    assert result["bucket_storage_bytes"] == 2_000_000_000
    assert result["account_storage_bytes"] == 2_400_002_000
    assert analytics_client.request["headers"]["Authorization"] == "Bearer analytics-secret-token"
    assert analytics_client.request["json"]["variables"]["startDate"].startswith("2026-08-24")
    assert billing_client.requests[0]["headers"]["Authorization"] == "Bearer billing-secret-token"


def test_r2_dashboard_falls_back_to_analytics_on_verified_billing_cycle():
    analytics_client = FakeAnalyticsClient(
        {
            "data": {
                "viewer": {
                    "accounts": [
                        {
                            "operations": [
                                {
                                    "sum": {"requests": 73},
                                    "dimensions": {"actionType": "PutObject"},
                                },
                                {
                                    "sum": {"requests": 14},
                                    "dimensions": {"actionType": "GetObject"},
                                },
                            ],
                            "storage": [],
                        }
                    ]
                }
            }
        }
    )
    billing_client = FakeBillingClient(
        {"errors": [{"message": "restricted"}], "success": False},
        {
            "success": True,
            "result": [
                {
                    "frequency": "monthly",
                    "state": "Provisioned",
                    "current_period_start": "2026-08-24T00:00:00Z",
                    "current_period_end": "2026-09-24T00:00:00Z",
                    "rate_plan": {"id": "r2", "public_name": "R2 Object Storage"},
                }
            ],
        },
        usage_status=403,
    )
    with temporary_settings(
        **R2_SETTINGS,
        cloudflare_analytics_api_token="analytics-secret-token",
        cloudflare_billing_api_token="billing-secret-token",
    ):
        result = cloudflare_usage.r2_usage_summary(
            now=cloudflare_usage.datetime(2026, 8, 24, 12, tzinfo=cloudflare_usage.timezone.utc),
            analytics_client=analytics_client,
            billing_client=billing_client,
            storage_client=FakeR2Client(),
        )

    assert result["billing_ready"] is False
    assert result["billing_period_ready"] is True
    assert result["billing_authoritative"] is False
    assert result["usage_source"] == "analytics_billing_period"
    assert result["class_a"]["used"] == 73
    assert result["class_b"]["used"] == 14
    assert analytics_client.request["json"]["variables"]["startDate"].startswith("2026-08-24")


def test_r2_dashboard_never_returns_analytics_token_in_errors():
    client = FakeAnalyticsClient(
        {"errors": [{"message": "invalid analytics-secret-token for account-id"}]}
    )
    billing_client = FakeBillingClient(
        {
            "success": True,
            "result": [
                {
                    "x_ProductFamilyName": "R2",
                    "x_BillableMetricName": "R2 Class A Operations",
                    "ConsumedQuantity": 1,
                    "BillingPeriodStart": "2026-08-24T00:00:00Z",
                    "BillingPeriodEnd": "2026-09-24T00:00:00Z",
                }
            ],
        }
    )
    with temporary_settings(
        **R2_SETTINGS,
        cloudflare_analytics_api_token="analytics-secret-token",
        cloudflare_billing_api_token="billing-secret-token",
    ):
        result = cloudflare_usage.r2_usage_summary(
            now=cloudflare_usage.datetime(2026, 8, 24, 12, tzinfo=cloudflare_usage.timezone.utc),
            analytics_client=client,
            billing_client=billing_client,
            storage_client=FakeR2Client(),
        )

    assert result["analytics_ready"] is False
    assert result["analytics_error"]
    assert "analytics-secret-token" not in result["analytics_error"]
    assert "billing-secret-token" not in result["analytics_error"]
    assert "account-id" not in result["analytics_error"]


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
