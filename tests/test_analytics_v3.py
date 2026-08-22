import asyncio
import json
from datetime import datetime, timezone

from app.services import analytics, cerebras, instagram


class FakeCursor(list):
    def sort(self, key, direction):
        reverse = direction < 0
        return FakeCursor(sorted(self, key=lambda item: item.get(key), reverse=reverse))

    def limit(self, amount):
        return FakeCursor(self[:amount])


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents

    def find(self, query):
        return FakeCursor(list(self.documents))

    def find_one(self, query):
        identifier = query.get("_id")
        return next((item for item in self.documents if item.get("_id") == identifier), None)


class FakeDatabase:
    def __init__(self, media):
        self.instagram_media = FakeCollection(media)
        self.analytics_state = FakeCollection([])
        self.analytics_reports = FakeCollection([])


class FakeGroqResponse:
    status_code = 200
    text = ""

    def json(self):
        report = {
            "summary": "Bilan prudent.",
            "recommendations": ["Tester deux créneaux."],
            "hook_findings": ["Comparer les hooks courts et longs."],
            "timing_findings": ["Échantillon limité."],
            "experiments": ["Publier deux variantes."],
            "cautions": ["Corrélation uniquement."],
        }
        return {"choices": [{"message": {"content": json.dumps(report)}}]}


class FakeGroqClient:
    last_payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json=None, headers=None):
        type(self).last_payload = json
        return FakeGroqResponse()


def test_oauth_requests_insights_permission():
    previous = (
        instagram.settings.instagram_app_id,
        instagram.settings.instagram_redirect_uri,
    )
    object.__setattr__(instagram.settings, "instagram_app_id", "test-app")
    object.__setattr__(instagram.settings, "instagram_redirect_uri", "https://studio.test/callback")
    try:
        url = instagram.build_authorize_url("state")
    finally:
        object.__setattr__(instagram.settings, "instagram_app_id", previous[0])
        object.__setattr__(instagram.settings, "instagram_redirect_uri", previous[1])

    assert "instagram_business_manage_insights" in url
    assert "instagram_business_content_publish" in url


def test_insight_payload_is_normalized_from_values_and_totals():
    result = analytics.normalize_insights(
        {
            "data": [
                {"name": "views", "values": [{"value": 1200}]},
                {"name": "reach", "total_value": {"value": 800}},
                {"name": "unsupported", "values": []},
            ]
        }
    )

    assert result == {"views": 1200, "reach": 800}


def test_dashboard_computes_totals_hours_hooks_and_deltas(monkeypatch):
    documents = [
        {
            "_id": "media-1",
            "title": "Premier Reel",
            "hook": "Tu veux voir ce spot ?",
            "media_kind": "reel",
            "timestamp": datetime(2026, 8, 17, 18, tzinfo=timezone.utc),
            "permalink": "https://instagram.com/p/one",
            "latest_metrics": {"views": 1000, "reach": 800, "likes": 50, "total_interactions": 80},
            "previous_metrics": {"views": 700, "reach": 600, "total_interactions": 50},
        },
        {
            "_id": "media-2",
            "title": "Deuxième Reel",
            "hook": "Un passage FPV en une prise",
            "media_kind": "reel",
            "timestamp": datetime(2026, 8, 17, 18, 30, tzinfo=timezone.utc),
            "permalink": "https://instagram.com/p/two",
            "latest_metrics": {"views": 500, "reach": 400, "likes": 30, "comments": 10},
        },
    ]
    monkeypatch.setattr(analytics, "database_configured", lambda: True)
    monkeypatch.setattr(analytics, "database", lambda: FakeDatabase(documents))

    dashboard = analytics.build_analytics_dashboard("UTC")

    assert dashboard["summary"]["media_count"] == 2
    assert dashboard["summary"]["views"] == 1500
    assert dashboard["summary"]["interactions"] == 120
    assert dashboard["best_times"][0]["weekday"] == "Lundi"
    assert dashboard["best_times"][0]["hour"] == 18
    assert dashboard["top_posts"][0]["delta_views"] == 300
    assert dashboard["top_posts"][0]["likes"] == 50
    assert dashboard["automatic_findings"]


def test_groq_analysis_never_sends_caption_or_complete_hook(monkeypatch):
    dashboard = {
        "summary": {"media_count": 3, "views": 2000, "reach": 1500, "interactions": 120},
        "best_times": [{"weekday": "Lundi", "hour": 18, "count": 3}],
        "top_posts": [
            {
                "caption": "CAPTION-PRIVEE-A-NE-PAS-ENVOYER",
                "hook": "HOOK-COMPLET-A-NE-PAS-ENVOYER ?",
                "media_kind": "reel",
                "views": 2000,
                "reach": 1500,
                "interactions": 120,
                "engagement_rate": 8,
                "permalink": "https://instagram.com/private",
            }
        ],
    }
    monkeypatch.setattr(cerebras.httpx, "AsyncClient", FakeGroqClient)
    previous_key = cerebras.settings.cerebras_api_key
    object.__setattr__(cerebras.settings, "cerebras_api_key", "test-only-key")
    try:
        report = asyncio.run(cerebras.analyze_instagram_performance(dashboard))
    finally:
        object.__setattr__(cerebras.settings, "cerebras_api_key", previous_key)

    transmitted = json.dumps(FakeGroqClient.last_payload, ensure_ascii=False)
    assert report["summary"] == "Bilan prudent."
    assert "CAPTION-PRIVEE-A-NE-PAS-ENVOYER" not in transmitted
    assert "HOOK-COMPLET-A-NE-PAS-ENVOYER" not in transmitted
    assert "instagram.com/private" not in transmitted
    user_content = FakeGroqClient.last_payload["messages"][1]["content"]
    assert '"views": 2000' in user_content
    assert '"contains_question": true' in user_content
