import secrets
from urllib.parse import quote

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings


SESSION_COOKIE = "instagram_studio_session"
SESSION_SALT = "instagram-studio-access"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.app_secret_key,
        salt=SESSION_SALT,
    )


def session_max_age_seconds() -> int:
    return max(1, settings.studio_session_hours) * 60 * 60


def access_control_configured() -> bool:
    return bool(settings.studio_access_code)


def access_code_matches(candidate: str) -> bool:
    if not access_control_configured():
        return False
    return secrets.compare_digest(
        candidate.encode("utf-8"),
        settings.studio_access_code.encode("utf-8"),
    )


def create_session_token() -> str:
    # The signed cookie contains no access code or API secret.
    return _serializer().dumps({"authenticated": True})


def request_is_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return False

    try:
        payload = _serializer().loads(
            token,
            max_age=session_max_age_seconds(),
        )
    except (BadSignature, SignatureExpired):
        return False

    return payload == {"authenticated": True}


def safe_next_path(value: str) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def login_redirect_path(path: str) -> str:
    return f"/login?next={quote(safe_next_path(path), safe='/')}"
