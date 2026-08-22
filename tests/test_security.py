from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


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
