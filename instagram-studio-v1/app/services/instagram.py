import asyncio
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings


class InstagramError(RuntimeError):
    pass


def api_url(path: str) -> str:
    return f"{settings.instagram_api_base}/{settings.instagram_api_version}/{path.lstrip('/')}"


def build_authorize_url(state: str) -> str:
    if not settings.instagram_app_id or not settings.instagram_redirect_uri:
        raise InstagramError("INSTAGRAM_APP_ID / INSTAGRAM_REDIRECT_URI non configurés.")
    params = {
        "client_id": settings.instagram_app_id,
        "redirect_uri": settings.instagram_redirect_uri,
        "response_type": "code",
        "scope": "instagram_business_basic,instagram_business_content_publish",
        "state": state,
    }
    return "https://www.instagram.com/oauth/authorize?" + urlencode(params)


async def exchange_code_for_token(code: str) -> dict[str, Any]:
    if not settings.instagram_app_id or not settings.instagram_app_secret or not settings.instagram_redirect_uri:
        raise InstagramError("Configuration OAuth Instagram incomplète.")
    data = {
        "client_id": settings.instagram_app_id,
        "client_secret": settings.instagram_app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": settings.instagram_redirect_uri,
        "code": code,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post("https://api.instagram.com/oauth/access_token", data=data)
    if response.status_code >= 400:
        raise InstagramError(f"Échange OAuth refusé: {response.text[:500]}")
    return response.json()


async def create_reel_container(*, user_id: str, access_token: str, video_url: str, caption: str) -> str:
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": access_token,
    }
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(api_url(f"{user_id}/media"), data=payload)
    if response.status_code >= 400:
        raise InstagramError(f"Création du Reel refusée: {response.text[:700]}")
    creation_id = response.json().get("id")
    if not creation_id:
        raise InstagramError("Instagram n'a pas renvoyé de creation_id.")
    return creation_id


async def get_container_status(*, creation_id: str, access_token: str) -> str:
    params = {"fields": "status_code,status", "access_token": access_token}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(api_url(creation_id), params=params)
    if response.status_code >= 400:
        raise InstagramError(f"Statut du média indisponible: {response.text[:500]}")
    data = response.json()
    return str(data.get("status_code") or data.get("status") or "UNKNOWN").upper()


async def wait_until_ready(*, creation_id: str, access_token: str, timeout_seconds: int = 180) -> None:
    elapsed = 0
    while elapsed < timeout_seconds:
        status = await get_container_status(creation_id=creation_id, access_token=access_token)
        if status in {"FINISHED", "PUBLISHED"}:
            return
        if status in {"ERROR", "EXPIRED"}:
            raise InstagramError(f"Traitement Instagram échoué ({status}).")
        await asyncio.sleep(5)
        elapsed += 5
    raise InstagramError("Instagram traite encore la vidéo. Réessaie dans quelques instants.")


async def publish_container(*, user_id: str, access_token: str, creation_id: str) -> str:
    payload = {"creation_id": creation_id, "access_token": access_token}
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(api_url(f"{user_id}/media_publish"), data=payload)
    if response.status_code >= 400:
        raise InstagramError(f"Publication refusée: {response.text[:700]}")
    media_id = response.json().get("id")
    if not media_id:
        raise InstagramError("Instagram n'a pas renvoyé l'id du média publié.")
    return media_id


async def publish_reel(*, user_id: str, access_token: str, video_url: str, caption: str) -> dict[str, str]:
    creation_id = await create_reel_container(
        user_id=user_id,
        access_token=access_token,
        video_url=video_url,
        caption=caption,
    )
    await wait_until_ready(creation_id=creation_id, access_token=access_token)
    media_id = await publish_container(
        user_id=user_id,
        access_token=access_token,
        creation_id=creation_id,
    )
    return {"creation_id": creation_id, "media_id": media_id}
