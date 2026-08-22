import asyncio
import base64
import hashlib
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.services.database import database, database_configured, utc_now
from app.services.instagram import InstagramError, refresh_long_lived_token


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.app_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _save_credentials_sync(
    *,
    user_id: str,
    access_token: str,
    expires_in: int,
) -> None:
    now = utc_now()
    database().instagram_credentials.update_one(
        {"_id": "primary"},
        {
            "$set": {
                "user_id": user_id,
                "encrypted_access_token": _fernet().encrypt(
                    access_token.encode("utf-8")
                ).decode("ascii"),
                "expires_at": now + timedelta(seconds=max(expires_in, 1)),
                "refreshed_at": now,
                "updated_at": now,
            }
        },
        upsert=True,
    )


async def save_credentials(
    *,
    user_id: str,
    access_token: str,
    expires_in: int,
) -> bool:
    if not database_configured() or not user_id or not access_token:
        return False
    await asyncio.to_thread(
        _save_credentials_sync,
        user_id=user_id,
        access_token=access_token,
        expires_in=expires_in,
    )
    return True


async def stored_credentials_exist() -> bool:
    if not database_configured():
        return False
    try:
        return await asyncio.to_thread(
            lambda: database().instagram_credentials.count_documents(
                {"_id": "primary"}, limit=1
            )
            > 0
        )
    except Exception:
        return False


def _load_credentials_sync() -> dict | None:
    document = database().instagram_credentials.find_one({"_id": "primary"})
    if not document:
        return None
    try:
        token = _fernet().decrypt(
            document["encrypted_access_token"].encode("ascii")
        ).decode("utf-8")
    except (KeyError, InvalidToken, ValueError):
        return None
    return {
        "user_id": str(document.get("user_id", "")),
        "access_token": token,
        "expires_at": document.get("expires_at"),
        "refreshed_at": document.get("refreshed_at"),
    }


async def resolve_instagram_credentials(
    *,
    refresh_if_needed: bool = True,
) -> tuple[str, str]:
    stored = None
    if database_configured():
        stored = await asyncio.to_thread(_load_credentials_sync)

    if not stored:
        return settings.instagram_user_id, settings.instagram_access_token

    user_id = stored["user_id"]
    access_token = stored["access_token"]
    refreshed_at = stored.get("refreshed_at")

    should_refresh = (
        refresh_if_needed
        and refreshed_at is not None
        and refreshed_at <= utc_now() - timedelta(days=30)
    )
    if not should_refresh:
        return user_id, access_token

    try:
        refreshed = await refresh_long_lived_token(access_token)
        new_token = str(refreshed["access_token"])
        expires_in = int(refreshed.get("expires_in", 5184000))
        await save_credentials(
            user_id=user_id,
            access_token=new_token,
            expires_in=expires_in,
        )
        return user_id, new_token
    except InstagramError:
        # Keep using the still-valid token; publication errors remain explicit.
        return user_id, access_token
