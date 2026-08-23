from contextlib import contextmanager

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.main import app
from app.routes import v2
from app.services import login_security
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


def test_login_uses_numeric_keypad_without_revealing_access_code():
    with temporary_settings(studio_access_code="123456"):
        with TestClient(app) as client:
            response = client.get("/login")

    assert response.status_code == 200
    assert 'type="password"' in response.text
    assert 'inputmode="numeric"' in response.text
    assert 'pattern="[0-9]*"' in response.text
    assert "123456" not in response.text


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


def test_login_attempts_are_limited_without_storing_sensitive_values():
    login_security.reset_local_login_security()
    headers = {"user-agent": "InstagramStudio-Security-Test/iPhone"}
    with temporary_settings(
        studio_access_code="123456",
        studio_cookie_secure=False,
        mongodb_uri="",
        login_max_attempts=3,
        login_window_minutes=15,
        login_lockout_minutes=15,
    ):
        with TestClient(app) as client:
            first = client.post(
                "/login", data={"access_code": "000000"}, headers=headers
            )
            second = client.post(
                "/login", data={"access_code": "000000"}, headers=headers
            )
            locked = client.post(
                "/login", data={"access_code": "000000"}, headers=headers
            )
            correct_while_locked = client.post(
                "/login", data={"access_code": "123456"}, headers=headers
            )

    assert first.status_code == 401
    assert second.status_code == 401
    assert locked.status_code == 429
    assert correct_while_locked.status_code == 429
    history = login_security.list_login_history()
    assert len(history) == 3
    assert all("client_hash" not in event for event in history)
    assert "123456" not in str(history)
    assert "000000" not in str(history)
    login_security.reset_local_login_security()


def test_security_push_is_sent_when_attempt_limit_is_reached(monkeypatch):
    login_security.reset_local_login_security()
    calls = []

    async def fake_send_notification(**kwargs):
        calls.append(kwargs)
        return 1

    monkeypatch.setattr(main_module, "send_notification", fake_send_notification)
    headers = {"user-agent": "Lockout-Notification-Test/iPhone Safari"}
    with temporary_settings(
        studio_access_code="123456",
        studio_cookie_secure=False,
        mongodb_uri="",
        login_max_attempts=3,
    ):
        with TestClient(app) as client:
            for _ in range(3):
                response = client.post(
                    "/login", data={"access_code": "000000"}, headers=headers
                )

    assert response.status_code == 429
    assert calls == [
        {
            "preference": "security_lockout",
            "title": "Accès au Studio temporairement bloqué",
            "body": "iPhone • Safari a été bloqué après 3 codes incorrects.",
            "url": "/?tab=settings",
            "tag": calls[0]["tag"],
        }
    ]
    assert "000000" not in str(calls)
    assert "123456" not in str(calls)
    assert DEFAULT_PREFERENCES["security_lockout"] is True
    login_security.reset_local_login_security()


def test_manual_device_block_rejects_correct_code_and_can_be_removed():
    login_security.reset_local_login_security()
    context = {
        "client_hash": "a" * 64,
        "device": "iPhone",
        "browser": "Safari",
    }
    with temporary_settings(mongodb_uri=""):
        login_security.record_login_success(context)
        login_security.set_device_blocked(context["client_hash"], True)
        blocked = login_security.login_attempt_status(context["client_hash"])
        security = login_security.list_login_security(
            current_client_hash="b" * 64
        )
        login_security.set_device_blocked(context["client_hash"], False)
        unblocked = login_security.login_attempt_status(context["client_hash"])

    assert blocked["allowed"] is False
    assert blocked["block_type"] == "manual"
    assert security["devices"][0]["blocked"] is True
    assert security["devices"][0]["block_type"] == "manual"
    assert unblocked["allowed"] is True
    login_security.reset_local_login_security()


def test_current_device_cannot_block_itself_from_settings():
    login_security.reset_local_login_security()
    headers = {"user-agent": "Current-Device-Test/iPhone Safari"}
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
        mongodb_uri="",
    ):
        with TestClient(app) as client:
            client.post(
                "/login",
                data={"access_code": "test-only-code"},
                headers=headers,
            )
            history = client.get(
                "/api/security/login-history", headers=headers
            ).json()
            current = next(device for device in history["devices"] if device["current"])
            response = client.post(
                f"/api/security/devices/{current['device_key']}/block",
                headers=headers,
            )

    assert response.status_code == 409
    assert "actuellement" in response.json()["error"]
    login_security.reset_local_login_security()


def test_blocked_device_loses_existing_session_and_correct_code_is_rejected():
    login_security.reset_local_login_security()
    headers = {"user-agent": "Blocked-Session-Test/iPhone Safari"}
    with temporary_settings(
        studio_access_code="123456",
        studio_cookie_secure=False,
        mongodb_uri="",
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "123456"}, headers=headers)
            history = client.get(
                "/api/security/login-history", headers=headers
            ).json()
            device_key = history["devices"][0]["device_key"]
            login_security.set_device_blocked(device_key, True)

            protected = client.get("/", headers=headers, follow_redirects=False)
            correct_code = client.post(
                "/login", data={"access_code": "123456"}, headers=headers
            )
            login_security.set_device_blocked(device_key, False)
            restored = client.post(
                "/login",
                data={"access_code": "123456"},
                headers=headers,
                follow_redirects=False,
            )

    assert protected.status_code == 303
    assert protected.headers["location"] == "/login"
    assert correct_code.status_code == 403
    assert "bon code" in correct_code.text
    assert restored.status_code == 303
    login_security.reset_local_login_security()


def test_login_history_endpoint_is_visible_in_settings():
    login_security.reset_local_login_security()
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
        mongodb_uri="",
    ):
        with TestClient(app) as client:
            client.post(
                "/login",
                data={"access_code": "test-only-code"},
                headers={"user-agent": "Mobile Safari iPhone"},
            )
            page = client.get("/")
            history = client.get("/api/security/login-history")

    assert page.status_code == 200
    assert 'id="loginHistory"' in page.text
    assert history.status_code == 200
    assert history.json()["events"][0]["success"] is True
    assert history.json()["events"][0]["device"] == "iPhone"
    assert "devices" in history.json()
    login_security.reset_local_login_security()


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


def test_instagram_token_alert_is_available_without_exposing_token():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
        mongodb_uri="",
        instagram_user_id="test-user",
        instagram_access_token="private-test-token",
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code"})
            page = client.get("/")
            health = client.get("/api/instagram/token-health")

    assert page.status_code == 200
    assert 'id="notifyToken"' in page.text
    assert DEFAULT_PREFERENCES["instagram_token"] is True
    assert health.status_code == 200
    assert health.json()["source"] == "environment"
    assert "private-test-token" not in health.text
    assert "private-test-token" not in page.text


def test_security_device_interface_and_notification_option_are_available():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code"})
            page = client.get("/")
            script = client.get("/static/app.js")

    assert 'id="notifySecurityLockout"' in page.text
    assert 'id="blockedDevicesSummary"' in page.text
    assert 'id="loginDevices"' in page.text
    assert "changeDeviceAccess" in script.text
    assert "security_lockout" in script.text


def test_music_finalization_prepares_files_for_native_share():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code"})
            page = client.get("/")
            script = client.get("/static/app.js")

    assert page.status_code == 200
    assert 'id="instagramSharePanel"' in page.text
    assert 'id="shareMediaBtn"' in page.text
    assert 'id="instagramShareFallback"' in page.text
    assert "fetchInstagramShareFiles" in script.text
    assert "navigator.canShare({files})" in script.text
    assert "navigator.share({files:prepared.files" in script.text
    assert "instagram://camera" in script.text


def test_preflight_rejects_caption_over_instagram_limit():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code"})
            response = client.post(
                "/api/publications/preflight",
                json={
                    "media_kind": "photo",
                    "media_items": [
                        {"url": "https://studio.example/photo.jpg", "media_type": "image"}
                    ],
                    "caption": "x" * 2201,
                    "publication_mode": "normal",
                    "workflow": "auto_publish",
                },
            )

    assert response.status_code == 400
    assert "2 200" in response.json()["error"]


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
    assert 'id="statsSort"' in page.text
    assert 'data-tab="settings"' in page.text
    assert 'id="settings"' in page.text


def test_phase_two_organization_controls_are_available():
    with temporary_settings(
        studio_access_code="test-only-code",
        studio_cookie_secure=False,
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "test-only-code"})
            page = client.get("/")
            script = client.get("/static/app.js")

    assert page.status_code == 200
    assert 'data-calendar-view="month"' in page.text
    assert 'data-calendar-view="week"' in page.text
    assert 'data-calendar-view="list"' in page.text
    assert 'id="librarySearch"' in page.text
    assert 'id="libraryUsageFilter"' in page.text
    assert 'id="carouselOrderHelp"' in page.text
    assert "startMediaPointerDrag" in script.text
    assert "moveScheduledPublication" in script.text
    assert "/schedule" in script.text


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
