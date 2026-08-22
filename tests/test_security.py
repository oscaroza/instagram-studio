from contextlib import contextmanager

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.main import app
from app.routes import v2
from app.services.push_notifications import DEFAULT_PREFERENCES


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


def test_protected_page_redirects_to_login():
    with temporary_settings(studio_access_code="test-only-code"):
        with TestClient(app) as client:
            response = client.get("/", follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"].startswith("/login")


def test_instagram_callback_requires_studio_session():
    with temporary_settings(studio_access_code="test-only-code"):
        with TestClient(app) as client:
            response = client.get(
                "/auth/instagram/callback?code=oauth-code&state=signed-state",
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert response.headers["location"].startswith("/login")


def test_login_sets_http_only_session_and_unlocks_studio():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            response = client.post(
                "/login",
                data={"access_code": "test-only-code", "next": "/"},
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert "HttpOnly" in response.headers["set-cookie"]
            assert "SameSite=lax" in response.headers["set-cookie"]
            assert client.get("/").status_code == 200


def test_successful_login_sends_privacy_safe_push(monkeypatch):
    calls = []

    async def fake_send_notification(**kwargs):
        calls.append(kwargs)
        return 1

    monkeypatch.setattr(main_module, "send_notification", fake_send_notification)
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            response = client.post(
                "/login",
                data={"access_code": "test-only-code", "next": "/"},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert calls == [
        {
            "preference": "studio_login",
            "title": "Connexion au Studio",
            "body": "Une connexion réussie vient d’être effectuée.",
            "url": "/?tab=settings",
            "tag": "studio-login",
        }
    ]
    assert "test-only-code" not in str(calls)
    assert DEFAULT_PREFERENCES["studio_login"] is True


def test_session_cookie_expires_after_five_idle_minutes_and_slides():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
        studio_idle_minutes=5,
    ):
        with TestClient(app) as client:
            login_response = client.post(
                "/login",
                data={"access_code": "test-only-code", "next": "/"},
                follow_redirects=False,
            )
            assert "Max-Age=300" in login_response.headers["set-cookie"]

            touch_response = client.post("/api/session/touch")
            assert touch_response.status_code == 200
            assert "Max-Age=300" in touch_response.headers["set-cookie"]


def test_jpeg_upload_accepts_real_jpeg_signature():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code"})
            response = client.post(
                "/api/upload",
                files={"file": ("photo.jpg", b"\xff\xd8\xfftest-jpeg", "image/jpeg")},
            )

    assert response.status_code == 200
    assert response.json()["media_type"] == "image"
    assert response.json()["url"].endswith(".jpg")


def test_fake_jpeg_is_rejected_and_not_exposed():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code"})
            response = client.post(
                "/api/upload",
                files={"file": ("photo.jpg", b"not-a-jpeg", "image/jpeg")},
            )

    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_wrong_code_does_not_create_session():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            response = client.post(
                "/login",
                data={"access_code": "wrong"},
            )
            assert response.status_code == 401
            assert "instagram_studio_session" not in response.cookies


def test_pwa_assets_are_public_and_service_worker_controls_root():
    with temporary_settings(studio_access_code="test-only-code"):
        with TestClient(app) as client:
            manifest = client.get("/static/manifest.webmanifest")
            worker = client.get("/sw.js")
            icon = client.get("/static/icons/apple-touch-icon.png")

            assert manifest.status_code == 200
            assert worker.status_code == 200
            assert worker.headers["service-worker-allowed"] == "/"
            assert icon.status_code == 200


def test_studio_sound_controls_and_chime_are_available():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code"})
            page = client.get("/")
            script = client.get("/static/app.js")

    assert page.status_code == 200
    assert 'id="studioSoundEnabled"' in page.text
    assert 'id="testStudioSoundBtn"' in page.text
    assert "playStudioChime()" in script.text
    assert "igstudio.studioSoundEnabled" in script.text


def test_v3_stats_dashboard_is_rendered_without_removing_settings():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code"})
            page = client.get("/")

    assert page.status_code == 200
    assert 'data-tab="stats"' in page.text
    assert 'id="stats"' in page.text
    assert 'id="syncStatsBtn"' in page.text
    assert 'id="analyzeStatsBtn"' in page.text
    assert 'id="statsAssistantReport"' in page.text
    assert 'id="notifyLogin"' in page.text
    assert 'data-tab="settings"' in page.text
    assert 'id="settings"' in page.text


def test_upload_stays_temporary_even_when_v2_storage_is_configured():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
        mongodb_uri="",
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code", "next": "/"})
            with temporary_settings(
                mongodb_uri="mongodb+srv://configured.invalid/",
                cloudinary_cloud_name="configured",
                cloudinary_api_key="configured",
                cloudinary_api_secret="configured",
            ):
                response = client.post(
                    "/api/upload",
                    files={"file": ("test.mp4", b"temporary-video", "video/mp4")},
                )

    assert response.status_code == 200
    assert response.json()["storage"] == "temporary"
    assert "/media/" in response.json()["url"]


def test_unexpected_api_failure_still_returns_safe_json(monkeypatch):
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
        mongodb_uri="",
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code", "next": "/"})
            monkeypatch.setattr(
                v2,
                "database",
                lambda: (_ for _ in ()).throw(RuntimeError("private database detail")),
            )
            with temporary_settings(mongodb_uri="mongodb://configured.invalid/"):
                response = client.get("/api/publications/calendar")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "ok": False,
        "error": "Service temporairement indisponible. Réessaie dans un instant.",
    }
