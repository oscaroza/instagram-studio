import asyncio
import json

from app.services import instagram


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
