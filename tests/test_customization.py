from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routes import v2
from app.services import preferences


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


class MemoryCollection:
    def __init__(self):
        self.document = None

    def find_one(self, query):
        if self.document and self.document.get("_id") == query.get("_id"):
            return self.document.copy()
        return None

    def replace_one(self, query, document, upsert=False):
        assert upsert is True
        assert document["_id"] == query["_id"]
        self.document = document.copy()

    def delete_one(self, query):
        if self.document and self.document.get("_id") == query.get("_id"):
            self.document = None


class MemoryDatabase:
    def __init__(self):
        self.studio_preferences = MemoryCollection()


def test_appearance_is_saved_in_mongodb_and_can_be_reset(monkeypatch):
    memory = MemoryDatabase()
    monkeypatch.setattr(preferences, "database_configured", lambda: True)
    monkeypatch.setattr(preferences, "database", lambda: memory)
    custom = {
        "accent": "#28b8d8",
        "background": "#06131d",
        "surface": "#0d2331",
        "text": "#f3fbff",
        "density": "compact",
        "radius": 14,
    }

    saved = preferences.save_appearance_preferences(custom)
    assert {key: saved[key] for key in custom} == custom
    assert saved["accent_text"] == "#08090d"
    assert preferences.get_appearance_preferences() == saved
    assert preferences.reset_appearance_preferences() == preferences.DEFAULT_APPEARANCE
    assert preferences.get_appearance_preferences() == preferences.DEFAULT_APPEARANCE


def test_appearance_rejects_untrusted_colors_and_layout_values():
    invalid_payloads = [
        {"accent": "red"},
        {"background": "url(javascript:alert(1))"},
        {"density": "microscopic"},
        {"radius": 100},
    ]
    for payload in invalid_payloads:
        try:
            preferences.normalize_appearance(payload)
        except ValueError:
            continue
        raise AssertionError(f"La préférence aurait dû être refusée : {payload}")


def test_appearance_api_requires_a_session():
    with temporary_settings(studio_access_code="123456", studio_cookie_secure=False):
        with TestClient(app) as client:
            response = client.put(
                "/api/preferences/appearance",
                json=preferences.DEFAULT_APPEARANCE,
            )

    assert response.status_code == 401


def test_authenticated_appearance_api_saves_validated_preferences(monkeypatch):
    received = []
    expected = preferences.normalize_appearance({"accent": "#28b8d8"})
    monkeypatch.setattr(
        v2,
        "save_appearance_preferences",
        lambda payload: received.append(payload) or expected,
    )
    with temporary_settings(studio_access_code="123456", studio_cookie_secure=False):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "123456"})
            response = client.put(
                "/api/preferences/appearance",
                json={"accent": "#28b8d8"},
            )

    assert response.status_code == 200
    assert received == [{"accent": "#28b8d8"}]
    assert response.json()["appearance"] == expected


def test_customization_and_foldable_sections_are_rendered():
    with temporary_settings(
        studio_access_code="123456",
        studio_cookie_secure=False,
        studio_idle_minutes=10,
    ):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "123456"})
            page = client.get("/")

    assert page.status_code == 200
    assert 'data-tab="customize"' in page.text
    assert 'id="customize"' in page.text
    assert 'id="saveAppearanceBtn"' in page.text
    assert 'id="appearanceAccent"' in page.text
    assert page.text.count('class="card collapsible-card result-card"') == 2
    assert 'class="assistant-chat nested-collapsible"' in page.text
    assert 'data-session-idle-seconds="600"' in page.text
