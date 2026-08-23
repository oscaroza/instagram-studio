import asyncio
import json
from contextlib import contextmanager

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.main import app
from app.routes import v2
from app.services import cerebras, login_security, passkeys


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


class MemoryCursor(list):
    def sort(self, key, direction):
        return MemoryCursor(sorted(self, key=lambda item: item.get(key), reverse=direction < 0))

    def limit(self, amount):
        return MemoryCursor(self[:amount])


class MemoryResult:
    def __init__(self, deleted_count=0):
        self.deleted_count = deleted_count


def matches(document, query):
    for key, expected in query.items():
        actual = document.get(key)
        if isinstance(expected, dict) and "$gt" in expected:
            if not actual or actual <= expected["$gt"]:
                return False
        elif actual != expected:
            return False
    return True


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    def find_one(self, query, projection=None):
        return next((item.copy() for item in self.documents if matches(item, query)), None)

    def find(self, query):
        return MemoryCursor([item.copy() for item in self.documents if matches(item, query)])

    def insert_one(self, document):
        self.documents.append(document.copy())

    def update_one(self, query, update, upsert=False):
        document = next((item for item in self.documents if matches(item, query)), None)
        if document is None and upsert:
            document = dict(query)
            document.update(update.get("$setOnInsert", {}))
            self.documents.append(document)
        if document is not None:
            document.update(update.get("$set", {}))

    def find_one_and_delete(self, query):
        for index, item in enumerate(self.documents):
            if matches(item, query):
                return self.documents.pop(index)
        return None

    def delete_one(self, query):
        for index, item in enumerate(self.documents):
            if matches(item, query):
                self.documents.pop(index)
                return MemoryResult(1)
        return MemoryResult()


class MemoryPasskeyDatabase:
    def __init__(self):
        self.passkey_accounts = MemoryCollection()
        self.passkeys = MemoryCollection()
        self.passkey_challenges = MemoryCollection()


def test_registration_options_use_server_challenge_once(monkeypatch):
    memory = MemoryPasskeyDatabase()
    monkeypatch.setattr(passkeys, "database_configured", lambda: True)
    monkeypatch.setattr(passkeys, "database", lambda: memory)
    with temporary_settings(app_base_url="https://studio.example.com"):
        result = passkeys.registration_options("device-hash")
        challenge = passkeys._consume_challenge(
            result["ceremony_id"], "registration", "device-hash"
        )

    assert result["public_key"]["rp"]["id"] == "studio.example.com"
    assert challenge
    try:
        passkeys._consume_challenge(
            result["ceremony_id"], "registration", "device-hash"
        )
    except passkeys.PasskeyError:
        pass
    else:
        raise AssertionError("Le challenge WebAuthn doit être utilisable une seule fois")


def test_passkey_management_requires_existing_session():
    with temporary_settings(studio_access_code="123456", studio_cookie_secure=False):
        with TestClient(app) as client:
            response = client.post("/api/passkeys/register/options")
            deletion = client.delete("/api/passkeys/credential-id")

    assert response.status_code == 401
    assert deletion.status_code == 401


def test_authenticated_user_can_register_passkey(monkeypatch):
    monkeypatch.setattr(
        v2,
        "registration_options",
        lambda client_hash: {"ceremony_id": "ceremony", "public_key": {"challenge": "AA"}},
    )
    monkeypatch.setattr(
        v2,
        "verify_registration",
        lambda ceremony_id, credential, client_hash, label: {"id": "credential", "label": label},
    )
    with temporary_settings(studio_access_code="123456", studio_cookie_secure=False):
        with TestClient(app) as client:
            client.post("/login", data={"access_code": "123456"})
            options = client.post("/api/passkeys/register/options")
            verified = client.post(
                "/api/passkeys/register/verify",
                json={"ceremony_id": "ceremony", "credential": {"id": "credential"}, "label": "iPhone"},
            )

    assert options.status_code == 200
    assert verified.status_code == 200
    assert verified.json()["passkey"]["label"] == "iPhone"


def test_verified_passkey_creates_session_and_login_notification(monkeypatch):
    login_security.reset_local_login_security()
    calls = []

    async def fake_notification(**kwargs):
        calls.append(kwargs)
        return 1

    monkeypatch.setattr(
        v2,
        "authentication_options",
        lambda client_hash: {"ceremony_id": "ceremony", "public_key": {"challenge": "AA"}},
    )
    monkeypatch.setattr(v2, "verify_authentication", lambda *args: {"label": "iPhone"})
    monkeypatch.setattr(v2, "send_notification", fake_notification)
    with temporary_settings(
        studio_access_code="123456",
        studio_cookie_secure=False,
        mongodb_uri="",
    ):
        with TestClient(app) as client:
            options = client.post("/api/passkeys/authenticate/options")
            response = client.post(
                "/api/passkeys/authenticate/verify",
                json={"ceremony_id": "ceremony", "credential": {"id": "credential"}, "next": "/?tab=stats"},
            )
            protected = client.get("/")

    assert options.status_code == 200
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.json()["next"] == "/?tab=stats"
    assert protected.status_code == 200
    assert calls[0]["body"] == "Une connexion Face ID/passkey vient d’être effectuée."
    login_security.reset_local_login_security()


def test_blocked_device_cannot_start_passkey_login(monkeypatch):
    monkeypatch.setattr(v2, "login_attempt_status", lambda client_hash: {"allowed": False})
    with temporary_settings(studio_access_code="123456", studio_cookie_secure=False):
        with TestClient(app) as client:
            response = client.post("/api/passkeys/authenticate/options")

    assert response.status_code == 403


class ChatResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "Teste deux hooks courts cette semaine."}}]}


class ChatClient:
    payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json=None, headers=None):
        type(self).payload = json
        return ChatResponse()


def test_conversation_sends_only_current_question_and_anonymized_stats(monkeypatch):
    dashboard = {
        "summary": {"media_count": 3, "views": 2000},
        "top_posts": [
            {
                "caption": "CAPTION-PRIVEE",
                "hook": "HOOK-SECRET ?",
                "permalink": "https://instagram.com/private",
                "views": 1000,
                "reach": 800,
                "interactions": 80,
                "engagement_rate": 10,
            }
        ],
    }
    monkeypatch.setattr(cerebras.httpx, "AsyncClient", ChatClient)
    with temporary_settings(cerebras_api_key="test-only-key"):
        answer = asyncio.run(
            cerebras.chat_instagram_performance(dashboard, "Que tester ensuite ?")
        )

    transmitted = json.dumps(ChatClient.payload, ensure_ascii=False)
    assert answer.startswith("Teste deux hooks")
    assert "Que tester ensuite ?" in transmitted
    assert "CAPTION-PRIVEE" not in transmitted
    assert "HOOK-SECRET" not in transmitted
    assert "instagram.com/private" not in transmitted
    assert "response_format" not in ChatClient.payload


def test_phase_four_controls_are_rendered(monkeypatch):
    monkeypatch.setattr(main_module, "passkey_available", lambda: True)
    with temporary_settings(studio_access_code="123456", studio_cookie_secure=False):
        with TestClient(app) as client:
            login = client.get("/login")
            client.post("/login", data={"access_code": "123456"})
            page = client.get("/")

    assert 'id="passkeyLoginBtn"' in login.text
    assert 'id="registerPasskeyBtn"' in page.text
    assert 'id="passkeyList"' in page.text
    assert 'id="assistantChatMessages"' in page.text
    assert 'id="assistantChatInput"' in page.text
