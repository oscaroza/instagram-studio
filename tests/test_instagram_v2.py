import asyncio
import json

from app.services import instagram
from app.config import settings


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeAsyncClient:
    response = FakeResponse({})
    last_request = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None):
        type(self).last_request = {"method": "GET", "url": url, "params": params}
        return type(self).response

    async def post(self, url, data=None):
        type(self).last_request = {"method": "POST", "url": url, "data": data}
        return type(self).response


def test_content_publishing_limit_uses_meta_values(monkeypatch):
    FakeAsyncClient.response = FakeResponse(
        {
            "data": [
                {
                    "quota_usage": 7,
                    "config": {"quota_total": 100, "quota_duration": 86400},
                }
            ]
        }
    )
    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        instagram.get_content_publishing_limit(
            user_id="ig-user",
            access_token="server-secret",
        )
    )

    assert result == {
        "used": 7,
        "total": 100,
        "remaining": 93,
        "duration_seconds": 86400,
    }
    assert FakeAsyncClient.last_request["params"]["fields"] == "quota_usage,config"


def test_short_token_is_exchanged_for_long_lived_token(monkeypatch):
    FakeAsyncClient.response = FakeResponse(
        {
            "access_token": "long-lived-test-token",
            "token_type": "bearer",
            "expires_in": 5184000,
        }
    )
    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeAsyncClient)

    previous_secret = settings.instagram_app_secret
    object.__setattr__(settings, "instagram_app_secret", "test-app-secret")
    try:
        result = asyncio.run(
            instagram.exchange_for_long_lived_token("short-lived-test-token")
        )
    finally:
        object.__setattr__(settings, "instagram_app_secret", previous_secret)

    assert result["access_token"] == "long-lived-test-token"
    assert result["expires_in"] == 5184000
    assert FakeAsyncClient.last_request["url"] == (
        "https://graph.instagram.com/access_token"
    )
    assert FakeAsyncClient.last_request["params"]["grant_type"] == (
        "ig_exchange_token"
    )


def test_long_lived_token_refresh_uses_instagram_refresh_endpoint(monkeypatch):
    FakeAsyncClient.response = FakeResponse(
        {"access_token": "refreshed-test-token", "expires_in": 5184000}
    )
    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(instagram.refresh_long_lived_token("old-test-token"))

    assert result["access_token"] == "refreshed-test-token"
    assert FakeAsyncClient.last_request["url"] == (
        "https://graph.instagram.com/refresh_access_token"
    )
    assert FakeAsyncClient.last_request["params"]["grant_type"] == (
        "ig_refresh_token"
    )


def test_normal_reel_payload_is_unchanged(monkeypatch):
    FakeAsyncClient.response = FakeResponse({"id": "creation-id"})
    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(
        instagram.create_reel_container(
            user_id="ig-user",
            access_token="server-secret",
            video_url="https://example.com/reel.mp4",
            caption="Caption",
        )
    )

    payload = FakeAsyncClient.last_request["data"]
    assert payload["media_type"] == "REELS"
    assert payload["share_to_feed"] == "true"
    assert "trial_params" not in payload


def test_trial_reel_adds_only_documented_trial_params(monkeypatch):
    FakeAsyncClient.response = FakeResponse({"id": "creation-id"})
    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(
        instagram.create_reel_container(
            user_id="ig-user",
            access_token="server-secret",
            video_url="https://example.com/reel.mp4",
            caption="Caption",
            trial=True,
        )
    )

    assert json.loads(FakeAsyncClient.last_request["data"]["trial_params"]) == {
        "graduation_strategy": "MANUAL"
    }


def test_single_photo_container_uses_image_url_and_caption(monkeypatch):
    FakeAsyncClient.response = FakeResponse({"id": "photo-container"})
    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        instagram.create_image_container(
            user_id="ig-user",
            access_token="server-secret",
            image_url="https://example.com/photo.jpg",
            caption="Caption photo",
        )
    )

    payload = FakeAsyncClient.last_request["data"]
    assert result == "photo-container"
    assert payload["image_url"] == "https://example.com/photo.jpg"
    assert payload["caption"] == "Caption photo"
    assert "is_carousel_item" not in payload
    assert "media_type" not in payload


def test_carousel_image_item_has_no_caption(monkeypatch):
    FakeAsyncClient.response = FakeResponse({"id": "child-container"})
    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(
        instagram.create_image_container(
            user_id="ig-user",
            access_token="server-secret",
            image_url="https://example.com/photo.jpg",
            caption="Must stay on parent",
            is_carousel_item=True,
        )
    )

    payload = FakeAsyncClient.last_request["data"]
    assert payload["is_carousel_item"] == "true"
    assert "caption" not in payload


def test_carousel_video_item_uses_video_container(monkeypatch):
    FakeAsyncClient.response = FakeResponse({"id": "video-child-container"})
    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        instagram.create_carousel_video_container(
            user_id="ig-user",
            access_token="server-secret",
            video_url="https://example.com/clip.mp4",
        )
    )

    payload = FakeAsyncClient.last_request["data"]
    assert result == "video-child-container"
    assert payload["media_type"] == "VIDEO"
    assert payload["video_url"] == "https://example.com/clip.mp4"
    assert payload["is_carousel_item"] == "true"
    assert "caption" not in payload


def test_carousel_parent_uses_ordered_children(monkeypatch):
    FakeAsyncClient.response = FakeResponse({"id": "carousel-container"})
    monkeypatch.setattr(instagram.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        instagram.create_carousel_container(
            user_id="ig-user",
            access_token="server-secret",
            children=["child-1", "child-2", "child-3"],
            caption="Caption carousel",
        )
    )

    payload = FakeAsyncClient.last_request["data"]
    assert result == "carousel-container"
    assert payload["media_type"] == "CAROUSEL"
    assert payload["children"] == "child-1,child-2,child-3"
    assert payload["caption"] == "Caption carousel"


def test_publish_carousel_accepts_ordered_images_and_videos(monkeypatch):
    created_items = []
    parent_children = []

    async def fake_create_item(**kwargs):
        created_items.append(kwargs["media_item"])
        return f"child-{len(created_items)}"

    async def fake_wait(**kwargs):
        return None

    async def fake_create_parent(**kwargs):
        parent_children.extend(kwargs["children"])
        return "parent-container"

    async def fake_publish(**kwargs):
        return "published-media"

    monkeypatch.setattr(instagram, "create_carousel_item_container", fake_create_item)
    monkeypatch.setattr(instagram, "wait_until_ready", fake_wait)
    monkeypatch.setattr(instagram, "create_carousel_container", fake_create_parent)
    monkeypatch.setattr(instagram, "publish_container", fake_publish)

    media_items = [
        {"url": "https://example.com/photo.jpg", "media_type": "image"},
        {"url": "https://example.com/clip.mp4", "media_type": "video"},
    ]
    result = asyncio.run(
        instagram.publish_carousel(
            user_id="ig-user",
            access_token="server-secret",
            media_items=media_items,
            caption="Carrousel mixte",
        )
    )

    assert created_items == media_items
    assert parent_children == ["child-1", "child-2"]
    assert result["media_id"] == "published-media"
