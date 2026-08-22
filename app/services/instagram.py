import asyncio
import json
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings


class InstagramError(RuntimeError):
    pass


# ============================================================
# URL API INSTAGRAM / META
# ============================================================

def api_url(path: str) -> str:
    return (
        f"{settings.instagram_api_base}/"
        f"{settings.instagram_api_version}/"
        f"{path.lstrip('/')}"
    )


def _safe_meta_error(text: str, *secret_values: str) -> str:
    safe_text = text
    for secret_value in secret_values:
        if secret_value:
            safe_text = safe_text.replace(secret_value, "[secret redacted]")
    return safe_text[:1200]


async def get_content_publishing_limit(
    *,
    user_id: str,
    access_token: str,
) -> dict[str, int]:
    """Return Meta's rolling publishing usage without exposing credentials."""
    if not user_id or not access_token:
        raise InstagramError(
            "Le compteur Instagram nécessite un User ID et un token configurés."
        )

    params = {
        "fields": "quota_usage,config",
        "access_token": access_token,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            api_url(f"{user_id}/content_publishing_limit"),
            params=params,
        )

    if response.status_code >= 400:
        raise InstagramError(
            "Compteur de publication Instagram indisponible : "
            f"{_safe_meta_error(response.text, access_token)}"
        )

    try:
        payload = response.json()
        item = payload["data"][0]
        used = int(item.get("quota_usage", 0))
        config = item.get("config") or {}
        total = int(config.get("quota_total", 100))
        duration = int(config.get("quota_duration", 86400))
    except (KeyError, IndexError, TypeError, ValueError):
        raise InstagramError(
            "Instagram a renvoyé un compteur de publication invalide."
        )

    return {
        "used": used,
        "total": total,
        "remaining": max(0, total - used),
        "duration_seconds": duration,
    }


# ============================================================
# INSTAGRAM LOGIN DIRECT
# ============================================================

def build_authorize_url(state: str) -> str:
    if (
        not settings.instagram_app_id
        or not settings.instagram_redirect_uri
    ):
        raise InstagramError(
            "INSTAGRAM_APP_ID / INSTAGRAM_REDIRECT_URI non configurés."
        )

    params = {
        "client_id": settings.instagram_app_id,
        "redirect_uri": settings.instagram_redirect_uri,
        "response_type": "code",
        "scope": (
            "instagram_business_basic,"
            "instagram_business_content_publish"
        ),
        "state": state,
    }

    return (
        "https://www.instagram.com/oauth/authorize?"
        + urlencode(params)
    )


async def exchange_code_for_token(
    code: str,
) -> dict[str, Any]:
    if (
        not settings.instagram_app_id
        or not settings.instagram_app_secret
        or not settings.instagram_redirect_uri
    ):
        raise InstagramError(
            "Configuration OAuth Instagram incomplète."
        )

    data = {
        "client_id": settings.instagram_app_id,
        "client_secret": settings.instagram_app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": settings.instagram_redirect_uri,
        "code": code,
    }

    async with httpx.AsyncClient(
        timeout=30
    ) as client:
        response = await client.post(
            "https://api.instagram.com/oauth/access_token",
            data=data,
        )

    if response.status_code >= 400:
        raise InstagramError(
            "Échange OAuth Instagram refusé : "
            f"{response.text[:700]}"
        )

    try:
        return response.json()

    except ValueError:
        raise InstagramError(
            "Instagram a renvoyé une réponse OAuth invalide."
        )


async def exchange_for_long_lived_token(
    short_lived_token: str,
) -> dict[str, Any]:
    """Exchange an unexpired OAuth token for Meta's long-lived token."""
    if not short_lived_token or not settings.instagram_app_secret:
        raise InstagramError(
            "Token court ou secret Instagram manquant pour l’échange longue durée."
        )

    params = {
        "grant_type": "ig_exchange_token",
        "client_secret": settings.instagram_app_secret,
        "access_token": short_lived_token,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            "https://graph.instagram.com/access_token",
            params=params,
        )

    if response.status_code >= 400:
        raise InstagramError(
            "Échange du token Instagram longue durée refusé : "
            f"{_safe_meta_error(response.text, short_lived_token, settings.instagram_app_secret)}"
        )

    try:
        payload = response.json()
    except ValueError:
        raise InstagramError(
            "Instagram a renvoyé une réponse longue durée invalide."
        )

    if not payload.get("access_token"):
        raise InstagramError(
            "Instagram n’a pas renvoyé de token longue durée."
        )

    return payload


# ============================================================
# FACEBOOK LOGIN FOR BUSINESS
# ANCIEN FLOW — CONSERVÉ TEMPORAIREMENT
# ============================================================

def build_facebook_business_login_url() -> str:
    if not settings.meta_app_id:
        raise InstagramError(
            "META_APP_ID non configuré."
        )

    if not settings.facebook_redirect_uri:
        raise InstagramError(
            "FACEBOOK_REDIRECT_URI non configuré."
        )

    extras = json.dumps(
        {
            "setup": {
                "channel": "IG_API_ONBOARDING"
            }
        },
        separators=(",", ":"),
    )

    params = {
        "client_id": settings.meta_app_id,
        "display": "page",
        "extras": extras,
        "redirect_uri": settings.facebook_redirect_uri,
        "response_type": "token",
        "scope": ",".join(
            [
                "instagram_basic",
                "instagram_content_publish",
                "pages_show_list",
                "pages_read_engagement",
            ]
        ),
    }

    return (
        f"https://www.facebook.com/"
        f"{settings.instagram_api_version}/"
        f"dialog/oauth?"
        + urlencode(params)
    )


# ============================================================
# RÉCUPÉRATION DU COMPTE INSTAGRAM
# ANCIEN FLOW FACEBOOK — CONSERVÉ TEMPORAIREMENT
# ============================================================

async def get_instagram_business_account(
    access_token: str,
) -> dict[str, str]:
    """
    Ancienne méthode Facebook Login.

    Recherche le compte Instagram professionnel
    relié aux Pages accessibles par le token Meta.
    """

    if not access_token:
        raise InstagramError(
            "Access token Meta vide."
        )

    # --------------------------------------------------------
    # 1. Récupération des Pages
    # --------------------------------------------------------

    params = {
        "fields": (
            "id,"
            "name,"
            "instagram_business_account,"
            "connected_instagram_account"
        ),
        "access_token": access_token,
    }

    async with httpx.AsyncClient(
        timeout=30
    ) as client:
        response = await client.get(
            api_url("me/accounts"),
            params=params,
        )

    if response.status_code >= 400:
        raise InstagramError(
            "Impossible de récupérer les Pages Facebook : "
            f"{response.text[:1200]}"
        )

    try:
        payload = response.json()

    except ValueError:
        raise InstagramError(
            "Meta a renvoyé une réponse invalide pour /me/accounts."
        )

    pages = payload.get(
        "data",
        [],
    )

    if not pages:
        raise InstagramError(
            "Meta accepte le token mais /me/accounts "
            "ne renvoie aucune Page Facebook."
        )

    # --------------------------------------------------------
    # 2. Recherche directe dans /me/accounts
    # --------------------------------------------------------

    for page in pages:
        page_id = str(
            page.get(
                "id",
                "",
            )
        )

        page_name = str(
            page.get(
                "name",
                "",
            )
        )

        instagram_business = page.get(
            "instagram_business_account"
        )

        connected_instagram = page.get(
            "connected_instagram_account"
        )

        if (
            isinstance(
                instagram_business,
                dict,
            )
            and instagram_business.get("id")
        ):
            return {
                "instagram_user_id": str(
                    instagram_business["id"]
                ),
                "page_id": page_id,
                "page_name": page_name,
                "instagram_source": (
                    "instagram_business_account"
                ),
            }

        if (
            isinstance(
                connected_instagram,
                dict,
            )
            and connected_instagram.get("id")
        ):
            return {
                "instagram_user_id": str(
                    connected_instagram["id"]
                ),
                "page_id": page_id,
                "page_name": page_name,
                "instagram_source": (
                    "connected_instagram_account"
                ),
            }

    # --------------------------------------------------------
    # 3. Vérification Page par Page
    # --------------------------------------------------------

    diagnostics = []

    async with httpx.AsyncClient(
        timeout=30
    ) as client:

        for page in pages:
            page_id = str(
                page.get(
                    "id",
                    "",
                )
            )

            page_name = str(
                page.get(
                    "name",
                    "",
                )
            )

            if not page_id:
                continue

            detail_params = {
                "fields": (
                    "id,"
                    "name,"
                    "instagram_business_account,"
                    "connected_instagram_account"
                ),
                "access_token": access_token,
            }

            detail_response = await client.get(
                api_url(page_id),
                params=detail_params,
            )

            if detail_response.status_code >= 400:
                diagnostics.append(
                    {
                        "page_id": page_id,
                        "page_name": page_name,
                        "detail_error": (
                            detail_response.text[:300]
                        ),
                    }
                )

                continue

            try:
                detail = detail_response.json()

            except ValueError:
                diagnostics.append(
                    {
                        "page_id": page_id,
                        "page_name": page_name,
                        "detail_error": (
                            "Réponse JSON invalide."
                        ),
                    }
                )

                continue

            instagram_business = detail.get(
                "instagram_business_account"
            )

            connected_instagram = detail.get(
                "connected_instagram_account"
            )

            diagnostics.append(
                {
                    "page_id": page_id,
                    "page_name": page_name,
                    "instagram_business_account": (
                        instagram_business.get("id")
                        if isinstance(
                            instagram_business,
                            dict,
                        )
                        else None
                    ),
                    "connected_instagram_account": (
                        connected_instagram.get("id")
                        if isinstance(
                            connected_instagram,
                            dict,
                        )
                        else None
                    ),
                }
            )

            if (
                isinstance(
                    instagram_business,
                    dict,
                )
                and instagram_business.get("id")
            ):
                return {
                    "instagram_user_id": str(
                        instagram_business["id"]
                    ),
                    "page_id": page_id,
                    "page_name": str(
                        detail.get(
                            "name",
                            page_name,
                        )
                    ),
                    "instagram_source": (
                        "instagram_business_account"
                    ),
                }

            if (
                isinstance(
                    connected_instagram,
                    dict,
                )
                and connected_instagram.get("id")
            ):
                return {
                    "instagram_user_id": str(
                        connected_instagram["id"]
                    ),
                    "page_id": page_id,
                    "page_name": str(
                        detail.get(
                            "name",
                            page_name,
                        )
                    ),
                    "instagram_source": (
                        "connected_instagram_account"
                    ),
                }

    # --------------------------------------------------------
    # Aucun compte trouvé
    # --------------------------------------------------------

    safe_pages = []

    for page in pages:
        safe_pages.append(
            {
                "id": page.get("id"),
                "name": page.get("name"),
                "instagram_business_account": (
                    page.get(
                        "instagram_business_account"
                    )
                ),
                "connected_instagram_account": (
                    page.get(
                        "connected_instagram_account"
                    )
                ),
            }
        )

    diagnostic_text = json.dumps(
        {
            "pages_from_me_accounts": safe_pages,
            "page_details": diagnostics,
        },
        ensure_ascii=False,
    )

    raise InstagramError(
        "Meta a accepté le token et les Pages sont accessibles, "
        "mais aucun compte Instagram relié n'a été trouvé. "
        "Diagnostic Meta : "
        f"{diagnostic_text[:3000]}"
    )


# ============================================================
# PUBLICATION REEL
# ============================================================

async def create_reel_container(
    *,
    user_id: str,
    access_token: str,
    video_url: str,
    caption: str,
    trial: bool = False,
) -> str:
    if not user_id:
        raise InstagramError(
            "INSTAGRAM_USER_ID vide."
        )

    if not access_token:
        raise InstagramError(
            "INSTAGRAM_ACCESS_TOKEN vide."
        )

    if not video_url.startswith(
        (
            "https://",
            "http://",
        )
    ):
        raise InstagramError(
            "L'URL de la vidéo doit être publique "
            "et commencer par https:// ou http://."
        )

    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": access_token,
    }

    if trial:
        # Meta documents trial_params for Trial Reels. Keep it opt-in so the
        # established normal Reel request remains byte-for-byte equivalent.
        payload["trial_params"] = json.dumps(
            {"graduation_strategy": "MANUAL"},
            separators=(",", ":"),
        )

    async with httpx.AsyncClient(
        timeout=45
    ) as client:
        response = await client.post(
            api_url(
                f"{user_id}/media"
            ),
            data=payload,
        )

    if response.status_code >= 400:
        raise InstagramError(
            "Création du Reel refusée : "
            f"{response.text[:1200]}"
        )

    try:
        data = response.json()

    except ValueError:
        raise InstagramError(
            "Instagram a accepté la création du Reel "
            "mais a renvoyé une réponse JSON invalide."
        )

    creation_id = data.get(
        "id"
    )

    if not creation_id:
        raise InstagramError(
            "Instagram n'a pas renvoyé de creation_id. "
            f"Réponse : {str(data)[:700]}"
        )

    return str(
        creation_id
    )


# ============================================================
# STATUT DU CONTAINER
# ============================================================

async def get_container_status(
    *,
    creation_id: str,
    access_token: str,
) -> dict[str, Any]:
    params = {
        "fields": "status_code,status",
        "access_token": access_token,
    }

    async with httpx.AsyncClient(
        timeout=30
    ) as client:
        response = await client.get(
            api_url(
                creation_id
            ),
            params=params,
        )

    if response.status_code >= 400:
        raise InstagramError(
            "Statut du média indisponible : "
            f"{response.text[:1200]}"
        )

    try:
        data = response.json()

    except ValueError:
        raise InstagramError(
            "Instagram a renvoyé une réponse "
            "de statut invalide."
        )

    return data


# ============================================================
# ATTENTE DU TRAITEMENT INSTAGRAM
# ============================================================

async def wait_until_ready(
    *,
    creation_id: str,
    access_token: str,
    timeout_seconds: int = 300,
) -> None:
    elapsed = 0
    poll_interval = 10

    while elapsed < timeout_seconds:
        data = await get_container_status(
            creation_id=creation_id,
            access_token=access_token,
        )

        status_code = str(
            data.get(
                "status_code",
                "UNKNOWN",
            )
        ).upper()

        status_detail = str(
            data.get(
                "status",
                "",
            )
        ).strip()

        # ----------------------------------------------------
        # Container prêt
        # ----------------------------------------------------

        if status_code in {
            "FINISHED",
            "PUBLISHED",
        }:
            return

        # ----------------------------------------------------
        # Instagram a rejeté le média
        # ----------------------------------------------------

        if status_code in {
            "ERROR",
            "EXPIRED",
        }:
            safe_response = {
                key: value
                for key, value in data.items()
                if key.lower()
                not in {
                    "access_token",
                    "token",
                }
            }

            safe_json = json.dumps(
                safe_response,
                ensure_ascii=False,
            )

            raise InstagramError(
                f"Traitement Instagram échoué ({status_code}). "
                f"Détail Meta : "
                f"{status_detail or 'aucun détail fourni'}. "
                f"Réponse Meta : {safe_json[:1500]}"
            )

        # ----------------------------------------------------
        # En cours
        # ----------------------------------------------------

        if status_code not in {
            "IN_PROGRESS",
            "UNKNOWN",
        }:
            print(
                "Statut Instagram inhabituel :",
                status_code,
                status_detail,
            )

        await asyncio.sleep(
            poll_interval
        )

        elapsed += poll_interval

    # --------------------------------------------------------
    # Timeout
    # --------------------------------------------------------

    final_data = await get_container_status(
        creation_id=creation_id,
        access_token=access_token,
    )

    final_status = str(
        final_data.get(
            "status_code",
            "UNKNOWN",
        )
    )

    final_detail = str(
        final_data.get(
            "status",
            "",
        )
    )

    raise InstagramError(
        "Instagram traite encore la vidéo après "
        f"{timeout_seconds} secondes. "
        f"Statut : {final_status}. "
        f"Détail : {final_detail or 'aucun'}."
    )


# ============================================================
# PUBLICATION DU CONTAINER
# ============================================================

async def publish_container(
    *,
    user_id: str,
    access_token: str,
    creation_id: str,
) -> str:
    payload = {
        "creation_id": creation_id,
        "access_token": access_token,
    }

    async with httpx.AsyncClient(
        timeout=45
    ) as client:
        response = await client.post(
            api_url(
                f"{user_id}/media_publish"
            ),
            data=payload,
        )

    if response.status_code >= 400:
        raise InstagramError(
            "Publication refusée : "
            f"{response.text[:1200]}"
        )

    try:
        data = response.json()

    except ValueError:
        raise InstagramError(
            "Instagram a renvoyé une réponse "
            "de publication invalide."
        )

    media_id = data.get(
        "id"
    )

    if not media_id:
        raise InstagramError(
            "Instagram n'a pas renvoyé "
            "l'id du média publié. "
            f"Réponse : {str(data)[:700]}"
        )

    return str(
        media_id
    )


# ============================================================
# PROCESS COMPLET DE PUBLICATION
# ============================================================

async def publish_reel(
    *,
    user_id: str,
    access_token: str,
    video_url: str,
    caption: str,
    trial: bool = False,
) -> dict[str, str]:
    # --------------------------------------------------------
    # 1. Création du container
    # --------------------------------------------------------

    creation_id = await create_reel_container(
        user_id=user_id,
        access_token=access_token,
        video_url=video_url,
        caption=caption,
        trial=trial,
    )

    # --------------------------------------------------------
    # 2. Attente du traitement de la vidéo
    # --------------------------------------------------------

    await wait_until_ready(
        creation_id=creation_id,
        access_token=access_token,
    )

    # --------------------------------------------------------
    # 3. Publication
    # --------------------------------------------------------

    media_id = await publish_container(
        user_id=user_id,
        access_token=access_token,
        creation_id=creation_id,
    )

    return {
        "creation_id": creation_id,
        "media_id": media_id,
        "publication_mode": "trial" if trial else "normal",
    }
