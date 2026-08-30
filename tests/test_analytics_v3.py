import asyncio
import json
from datetime import datetime, timedelta, timezone

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
    def __init__(self, media, snapshots=None, state=None):
        self.instagram_media = FakeCollection(media)
        self.instagram_insight_snapshots = FakeCollection(snapshots or [])
        self.analytics_state = FakeCollection(state or [])
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


class FakeContentIdeasResponse:
    status_code = 200
    text = ""

    def json(self):
        report = {
            "diagnosis": "Les statistiques justifient trois tests différents.",
            "ideas": [
                {
                    "title": f"Concept {index}",
                    "objective": "Partages",
                    "why": "Hypothèse à tester sur un échantillon limité.",
                    "hook": "Tu préfères A ou B ?",
                    "protocol": [
                        "0–2 s — Montrer A et B • Texte : Tu préfères lequel ?",
                        "2–8 s — Filmer la version A",
                        "8–14 s — Filmer la version B",
                    ],
                    "cta": "Choisis A ou B en commentaire.",
                    "success_metric": "Comparer le taux de commentaires.",
                }
                for index in range(1, 4)
            ],
        }
        return {"choices": [{"message": {"content": json.dumps(report)}}]}


class FakeContentIdeasClient(FakeGroqClient):
    async def post(self, url, json=None, headers=None):
        type(self).last_payload = json
        return FakeContentIdeasResponse()


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


def test_account_insights_and_follow_breakdown_are_normalized():
    metrics = analytics.normalize_account_insights(
        {
            "data": [
                {"name": "views", "total_value": {"value": 16444}},
                {"name": "reach", "total_value": {"value": 7364}},
                {"name": "total_interactions", "total_value": {"value": 797}},
            ]
        }
    )
    followers = analytics.normalize_follow_breakdown(
        {
            "data": [
                {
                    "name": "follows_and_unfollows",
                    "total_value": {
                        "breakdowns": [
                            {
                                "dimension_keys": ["follow_type"],
                                "results": [
                                    {"dimension_values": ["FOLLOWER"], "value": 20},
                                    {"dimension_values": ["NON_FOLLOWER"], "value": 29},
                                ],
                            }
                        ]
                    },
                }
            ]
        }
    )

    assert metrics == {"views": 16444, "reach": 7364, "total_interactions": 797}
    assert followers == {"follows": 20, "unfollows": 29, "net_follows": -9}


def test_ninety_day_account_insights_sum_only_additive_metrics(monkeypatch):
    calls = []

    async def window(**kwargs):
        calls.append((kwargs["start"], kwargs["end"]))
        return {
            "metrics": {
                "views": 1000,
                "reach": 800,
                "accounts_engaged": 100,
                "total_interactions": 50,
                "follows": 7,
                "unfollows": 3,
            },
            "errors": [],
        }

    monkeypatch.setattr(analytics, "_fetch_account_window_insights", window)
    now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    result = asyncio.run(
        analytics.fetch_account_period_insights(
            user_id="instagram-user",
            access_token="instagram-token",
            days=90,
            client=None,
            now=now,
        )
    )

    assert len(calls) == 3
    assert all(end - start == timedelta(days=30) for start, end in calls)
    assert result["metrics"]["views"] == 3000
    assert result["metrics"]["total_interactions"] == 150
    assert result["metrics"]["follows"] == 21
    assert result["metrics"]["unfollows"] == 9
    assert result["metrics"]["net_follows"] == 12
    assert "reach" not in result["metrics"]
    assert "accounts_engaged" not in result["metrics"]
    assert "reach" in result["unavailable_metrics"]
    assert result["chunk_count"] == 3
    assert result["display_start"] == "2026-06-01"
    assert result["display_end"] == "2026-08-29"


def test_instagram_sync_reports_real_progress(monkeypatch):
    saved_state = {}

    class AnalyticsState:
        def find_one(self, query):
            return None

        def update_one(self, query, update, **kwargs):
            saved_state.update(update.get("$set") or {})
            return None

    class SyncDatabase:
        analytics_state = AnalyticsState()

    async def credentials():
        return "instagram-user", "instagram-token"

    async def media_list(**kwargs):
        return [{"id": "one"}, {"id": "two"}]

    async def insights(**kwargs):
        return {"views": 100, "reach": 80}

    async def profile(**kwargs):
        return {"id": "instagram-user", "media_count": 100, "followers_count": 366}

    async def account_period(**kwargs):
        days = kwargs["days"]
        return {
            "days": days,
            "start": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "end": datetime(2026, 8, 31, tzinfo=timezone.utc),
            "metrics": {"views": 16444},
            "available_metrics": ["views"],
            "errors": [],
        }

    monkeypatch.setattr(analytics, "database_configured", lambda: True)
    monkeypatch.setattr(analytics, "database", lambda: SyncDatabase())
    monkeypatch.setattr(analytics, "resolve_instagram_credentials", credentials)
    monkeypatch.setattr(analytics, "list_instagram_media", media_list)
    monkeypatch.setattr(analytics, "fetch_media_insights", insights)
    monkeypatch.setattr(analytics, "fetch_account_profile", profile)
    monkeypatch.setattr(analytics, "fetch_account_period_insights", account_period)
    monkeypatch.setattr(analytics, "_store_media_snapshot", lambda *args: None)

    try:
        result = asyncio.run(analytics.sync_instagram_analytics())
        progress = analytics.get_analytics_sync_progress()
    finally:
        analytics._set_sync_progress(
            running=False,
            phase="idle",
            percent=0.0,
            current=0,
            total=0,
            message="Prêt à synchroniser.",
            started_at=None,
            finished_at=None,
        )

    assert result["metrics_updated"] == 2
    assert progress["running"] is False
    assert progress["phase"] == "complete"
    assert progress["percent"] == 100
    assert progress["current"] == 2
    assert progress["total"] == 2
    assert result["account_profile"]["media_count"] == 100
    assert result["account_insights"]["30"]["metrics"]["views"] == 16444
    assert saved_state["account_profile"]["followers_count"] == 366


def test_dashboard_computes_totals_hours_hooks_and_deltas(monkeypatch):
    documents = [
        {
            "_id": "media-1",
            "title": "Premier Reel",
            "hook": "Tu veux voir ce spot ?",
            "media_kind": "reel",
            "timestamp": datetime(2026, 8, 17, 18, tzinfo=timezone.utc),
            "permalink": "https://instagram.com/p/one",
            "media_product_type": "REELS",
            "latest_metrics": {
                "views": 1000,
                "reach": 800,
                "likes": 50,
                "comments": 12,
                "saved": 10,
                "shares": 8,
                "total_interactions": 80,
                "ig_reels_avg_watch_time": 4250,
                "ig_reels_video_view_total_time": 420000,
                "clips_replays_count": 160,
                "reels_skip_rate": 0.21,
            },
            "previous_metrics": {
                "views": 700,
                "reach": 600,
                "likes": 40,
                "comments": 8,
                "saved": 6,
                "shares": 4,
                "total_interactions": 50,
            },
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
    monkeypatch.setattr(
        analytics,
        "utc_now",
        lambda: datetime(2026, 8, 23, 12, tzinfo=timezone.utc),
    )

    dashboard = analytics.build_analytics_dashboard("UTC")

    assert dashboard["summary"]["media_count"] == 2
    assert dashboard["summary"]["views"] == 1500
    assert dashboard["summary"]["interactions"] == 120
    assert dashboard["best_times"][0]["weekday"] == "Lundi"
    assert dashboard["best_times"][0]["hour"] == 18
    assert dashboard["top_posts"][0]["delta_views"] == 300
    assert dashboard["top_posts"][0]["likes"] == 50
    assert dashboard["top_posts"][0]["delta_reach"] == 200
    assert dashboard["top_posts"][0]["delta_saved"] == 4
    assert dashboard["top_posts"][0]["save_rate"] == 1.25
    assert dashboard["top_posts"][0]["views_per_reached_account"] == 1.25
    assert dashboard["top_posts"][0]["avg_watch_time_ms"] == 4250
    assert dashboard["top_posts"][0]["replays"] == 160
    assert "reels_skip_rate" in dashboard["top_posts"][0]["available_metrics"]
    assert dashboard["top_posts"][0]["media_product_type"] == "REELS"
    assert dashboard["automatic_findings"]


def test_dashboard_separates_official_account_totals_from_analyzed_media(monkeypatch):
    documents = [
        {
            "_id": "media-1",
            "media_kind": "reel",
            "timestamp": datetime(2026, 8, 20, tzinfo=timezone.utc),
            "latest_metrics": {"views": 1000, "reach": 800, "total_interactions": 80},
        },
        {
            "_id": "media-2",
            "media_kind": "photo",
            "timestamp": datetime(2026, 8, 21, tzinfo=timezone.utc),
            "latest_metrics": {"views": 500, "reach": 400, "total_interactions": 40},
        },
    ]
    state = [
        {
            "_id": "primary",
            "account_profile": {
                "username": "oskyview",
                "media_count": 100,
                "followers_count": 366,
                "follows_count": 107,
            },
            "account_insights": {
                "30": {
                    "days": 30,
                    "start": datetime(2026, 7, 31, tzinfo=timezone.utc),
                    "end": datetime(2026, 8, 30, tzinfo=timezone.utc),
                    "metrics": {
                        "views": 16444,
                        "reach": 7364,
                        "total_interactions": 797,
                        "follows": 20,
                        "unfollows": 29,
                        "net_follows": -9,
                    },
                    "available_metrics": [
                        "views",
                        "reach",
                        "total_interactions",
                        "follows",
                        "unfollows",
                        "net_follows",
                    ],
                    "errors": [],
                }
            },
        }
    ]
    monkeypatch.setattr(analytics, "database_configured", lambda: True)
    monkeypatch.setattr(analytics, "database", lambda: FakeDatabase(documents, state=state))
    monkeypatch.setattr(
        analytics,
        "utc_now",
        lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    )

    dashboard = analytics.build_analytics_dashboard("Europe/Paris", period_days=30)

    assert dashboard["account"]["profile"]["media_count"] == 100
    assert dashboard["account"]["period"]["metrics"]["views"] == 16444
    assert dashboard["account"]["period"]["metrics"]["net_follows"] == -9
    assert dashboard["summary"]["media_count"] == 2
    assert dashboard["summary"]["views"] == 1500


def test_dashboard_compares_periods_and_builds_real_growth_series(monkeypatch):
    documents = [
        {
            "_id": "current-media",
            "title": "Publication actuelle",
            "media_kind": "reel",
            "timestamp": datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
            "latest_metrics": {"views": 1000, "reach": 800, "total_interactions": 80},
        },
        {
            "_id": "previous-media",
            "title": "Publication précédente",
            "media_kind": "photo",
            "timestamp": datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
            "latest_metrics": {"views": 500, "reach": 400, "total_interactions": 40},
        },
    ]
    snapshots = [
        {
            "media_id": "current-media",
            "captured_at": datetime(2026, 8, 15, 10, tzinfo=timezone.utc),
            "metrics": {"views": 500, "reach": 400, "total_interactions": 40},
        },
        {
            "media_id": "current-media",
            "captured_at": datetime(2026, 8, 20, 10, tzinfo=timezone.utc),
            "metrics": {"views": 900, "reach": 700, "total_interactions": 70},
        },
        {
            "media_id": "previous-media",
            "captured_at": datetime(2026, 8, 20, 10, tzinfo=timezone.utc),
            "metrics": {"views": 200, "reach": 150, "total_interactions": 15},
        },
        {
            "media_id": "current-media",
            "captured_at": datetime(2026, 8, 22, 10, tzinfo=timezone.utc),
            "metrics": {"views": 1000, "reach": 800, "total_interactions": 80},
        },
    ]
    monkeypatch.setattr(analytics, "database_configured", lambda: True)
    monkeypatch.setattr(analytics, "database", lambda: FakeDatabase(documents, snapshots))
    monkeypatch.setattr(
        analytics,
        "utc_now",
        lambda: datetime(2026, 8, 23, 12, tzinfo=timezone.utc),
    )

    dashboard = analytics.build_analytics_dashboard("UTC", period_days=7)

    comparison = dashboard["period_comparison"]
    assert comparison["days"] == 7
    assert comparison["current"]["media_count"] == 1
    assert comparison["previous"]["media_count"] == 1
    assert comparison["changes"]["views"] == 100
    assert len(dashboard["growth_series"]) == 3
    assert dashboard["growth_series"][0]["views"] == 500
    assert dashboard["growth_series"][-1]["views"] == 1200
    assert dashboard["growth_series"][-1]["delta_views"] == 700


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
    response_format = FakeGroqClient.last_payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]["additionalProperties"] is False
    assert FakeGroqClient.last_payload["reasoning_effort"] == "low"
    assert FakeGroqClient.last_payload["max_completion_tokens"] == 4096


def test_growth_ideas_use_stats_and_brief_without_historical_text(monkeypatch):
    dashboard = {
        "summary": {"media_count": 8, "views": 9000, "reach": 7000, "interactions": 360},
        "best_times": [{"weekday": "Samedi", "hour": 18, "count": 4}],
        "top_posts": [
            {
                "caption": "CAPTION-PRIVEE",
                "hook": "HOOK-COMPLET-PRIVE ?",
                "media_kind": "reel",
                "views": 3000,
                "reach": 2200,
                "interactions": 180,
                "engagement_rate": 8.1,
                "permalink": "https://instagram.com/private",
            }
        ],
    }
    monkeypatch.setattr(cerebras.httpx, "AsyncClient", FakeContentIdeasClient)
    previous_key = cerebras.settings.cerebras_api_key
    object.__setattr__(cerebras.settings, "cerebras_api_key", "test-only-key")
    try:
        report = asyncio.run(
            cerebras.generate_growth_content_ideas(
                dashboard,
                "-15 abonnés net. iPhone 16 Pro uniquement, deux heures sur la côte.",
            )
        )
    finally:
        object.__setattr__(cerebras.settings, "cerebras_api_key", previous_key)

    transmitted = json.dumps(FakeContentIdeasClient.last_payload, ensure_ascii=False)
    assert len(report["ideas"]) == 3
    assert report["ideas"][0]["shots"][0].startswith("0–2 s")
    assert "-15 abonnés net" in transmitted
    user_content = FakeContentIdeasClient.last_payload["messages"][1]["content"]
    assert '"views": 9000' in user_content
    assert "CAPTION-PRIVEE" not in transmitted
    assert "HOOK-COMPLET-PRIVE" not in transmitted
    assert "instagram.com/private" not in transmitted
    response_format = FakeContentIdeasClient.last_payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]["additionalProperties"] is False
    idea_properties = response_format["json_schema"]["schema"]["properties"]["ideas"]["items"]["properties"]
    assert set(idea_properties) == {
        "title",
        "objective",
        "why",
        "hook",
        "protocol",
        "cta",
        "success_metric",
    }
