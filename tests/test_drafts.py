import asyncio
import json
from datetime import datetime, timezone

from bson import ObjectId

from app.routes import v2
from app.services.drafts import draft_for_client, normalize_draft


def test_draft_normalization_keeps_only_small_metadata_and_media_references():
    draft = normalize_draft(
        {
            "clientId": "iphone-draft",
            "mediaKind": "carousel_video",
            "calendarEntryTitle": "Week-end à Annecy",
            "caption": "Une légende",
            "scheduleEnabled": True,
            "muteAudio": True,
            "secret": "ne doit jamais être stocké",
            "mediaItemsJson": json.dumps(
                [
                    {
                        "url": "https://media.example/video.mp4",
                        "library_id": "media-1",
                        "media_type": "video",
                        "name": "plan-lac.mp4",
                    }
                ]
            ),
        }
    )

    assert draft["client_id"] == "iphone-draft"
    assert draft["mediaKind"] == "carousel_video"
    assert draft["scheduleEnabled"] is True
    assert draft["muteAudio"] is True
    assert draft["imageOptimizationEnabled"] is True
    assert draft["media_items"][0]["library_id"] == "media-1"
    assert "secret" not in draft
    assert "mediaItemsJson" not in draft


def test_draft_rejects_embedded_binary_data_urls():
    try:
        normalize_draft(
            {
                "mediaItemsJson": json.dumps(
                    [{"url": "data:image/jpeg;base64,AAAA", "media_type": "image"}]
                )
            }
        )
    except ValueError as exc:
        assert "URL publique" in str(exc)
    else:
        raise AssertionError("Une image encodée dans MongoDB aurait dû être refusée.")


def test_draft_serialization_returns_frontend_shape():
    client = draft_for_client(
        {
            "_id": ObjectId("64f000000000000000000001"),
            "client_id": "mac-draft",
            "mediaKind": "photo",
            "media_items": [
                {
                    "url": "https://media.example/photo.jpg",
                    "library_id": "media-2",
                    "media_type": "image",
                }
            ],
            "updated_at": datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
        }
    )

    assert client["id"] == "64f000000000000000000001"
    assert client["clientId"] == "mac-draft"
    assert json.loads(client["mediaItemsJson"])[0]["library_id"] == "media-2"
    assert client["savedAt"].startswith("2026-08-31T12:00:00")


def test_draft_api_reports_saved_document(monkeypatch):
    expected = {"id": "draft-id", "clientId": "client-id"}
    monkeypatch.setattr(v2, "database_configured", lambda: True)
    monkeypatch.setattr(v2, "save_draft", lambda payload: expected)

    result = asyncio.run(v2.upsert_draft({"clientId": "client-id"}))

    assert result == {"ok": True, "draft": expected}
