import asyncio
import os
import secrets
import uuid
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)

from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)

from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from itsdangerous import (
    BadSignature,
    URLSafeSerializer,
)

from app.config import settings

from app.services.cerebras import (
    CerebrasError,
    generate_caption,
)

from app.services.instagram import (
    InstagramError,
    build_authorize_url,
    build_facebook_business_login_url,
    exchange_code_for_token,
    exchange_for_long_lived_token,
    get_content_publishing_limit,
    get_instagram_business_account,
    publish_carousel,
    publish_photo,
    publish_reel,
    publish_story,
)
from app.routes.v2 import publication_media_items, router as v2_router
from app.services.media_storage import (
    media_storage_configured,
    storage_provider_label,
)
from app.services.database import (
    database,
    database_configured,
    utc_now,
)
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.push_notifications import send_notification
from app.services.token_store import (
    resolve_instagram_credentials,
    save_credentials,
    stored_credentials_exist,
)
from app.services.publication_safety import (
    claim_publication,
    publication_checks,
    publication_fingerprint,
    release_publication_claim,
)
from app.services.login_security import (
    attach_login_device_cookie,
    device_is_manually_blocked,
    login_attempt_status,
    login_client_context,
    record_login_failure,
    record_login_success,
)
from app.services.passkeys import passkey_available
from app.services.preferences import DEFAULT_APPEARANCE, get_appearance_preferences
from app.security import (
    SESSION_COOKIE,
    access_code_matches,
    access_control_configured,
    create_session_token,
    login_redirect_path,
    request_is_authenticated,
    safe_next_path,
    session_max_age_seconds,
)
from app.v2.capabilities import V2_MODULES, publishing_capabilities


BASE_DIR = Path(
    __file__
).resolve().parent

UPLOAD_DIR = (
    BASE_DIR
    / "uploads"
)

UPLOAD_DIR.mkdir(
    exist_ok=True
)


app = FastAPI(
    title="Instagram Studio V2",
    version="2.0.0",
)


app.mount(
    "/static",
    StaticFiles(
        directory=BASE_DIR / "static"
    ),
    name="static",
)

app.include_router(v2_router)


templates = Jinja2Templates(
    directory=BASE_DIR / "templates"
)


@app.on_event("startup")
async def startup_services():
    if database_configured():
        try:
            start_scheduler()
        except Exception:
            # MongoDB ne doit jamais empêcher la V1 de démarrer.
            pass


@app.on_event("shutdown")
async def shutdown_services():
    stop_scheduler()


serializer = URLSafeSerializer(
    settings.app_secret_key,
    salt="instagram-oauth",
)


PUBLIC_PATHS = {
    "/health",
    "/login",
    "/sw.js",
    "/auth/facebook/callback",
    "/api/passkeys/authenticate/options",
    "/api/passkeys/authenticate/verify",
}


@app.middleware("http")
async def require_studio_session(request: Request, call_next):
    path = request.url.path
    public = (
        path in PUBLIC_PATHS
        or path.startswith("/static/")
        or path.startswith("/media/")
    )

    authenticated = False if public else request_is_authenticated(request)
    device_blocked = False
    if authenticated:
        client_context = login_client_context(request)
        device_blocked = await asyncio.to_thread(
            device_is_manually_blocked, client_context["client_hash"]
        )
        if device_blocked:
            authenticated = False
    try:
        if device_blocked and path.startswith("/api/"):
            response = JSONResponse(
                {
                    "ok": False,
                    "error": "Cet appareil a été bloqué dans les Réglages du Studio.",
                },
                status_code=403,
            )
        elif device_blocked:
            response = RedirectResponse("/login", status_code=303)
        elif public or authenticated:
            response = await call_next(request)
        elif path.startswith("/api/"):
            response = JSONResponse(
                {"ok": False, "error": "Session expirée ou accès non autorisé."},
                status_code=401,
            )
        else:
            response = RedirectResponse(
                login_redirect_path(path),
                status_code=303,
            )
    except Exception:
        if not path.startswith("/api/"):
            raise
        response = JSONResponse(
            {
                "ok": False,
                "error": "Service temporairement indisponible. Réessaie dans un instant.",
            },
            status_code=503,
        )

    if not path.startswith("/static/") and not path.startswith("/media/"):
        response.headers["Cache-Control"] = "no-store"
    if authenticated and path != "/logout" and response.status_code < 400:
        response.set_cookie(
            key=SESSION_COOKIE,
            value=create_session_token(),
            max_age=session_max_age_seconds(),
            httponly=True,
            secure=settings.studio_cookie_secure,
            samesite="lax",
            path="/",
        )
    if device_blocked:
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=settings.studio_cookie_secure,
            httponly=True,
            samesite="lax",
        )
    attach_login_device_cookie(request, response)
    return response


def json_error(
    message: str,
    status_code: int = 400,
) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": message,
        },
        status_code=status_code,
    )


# ============================================================
# STUDIO ACCESS
# ============================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    client_context = login_client_context(request)
    attempt_status = await asyncio.to_thread(
        login_attempt_status, client_context["client_hash"]
    )
    manually_blocked = attempt_status.get("block_type") == "manual"
    passkey_ready = await asyncio.to_thread(passkey_available)
    if request_is_authenticated(request) and not manually_blocked:
        return RedirectResponse(safe_next_path(next), status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "configured": access_control_configured(),
            "next_path": safe_next_path(next),
            "error": (
                "Cet appareil et ce navigateur ont été bloqués dans le Studio."
                if manually_blocked
                else ""
            ),
            "access_blocked": manually_blocked,
            "passkey_ready": passkey_ready,
        },
    )


@app.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    background_tasks: BackgroundTasks,
    access_code: str = Form(...),
    next: str = Form("/"),
):
    next_path = safe_next_path(next)
    client_context = login_client_context(request)
    passkey_ready = await asyncio.to_thread(passkey_available)

    if not access_control_configured():
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "configured": False,
                "next_path": next_path,
                "error": "STUDIO_ACCESS_CODE doit être configuré côté serveur.",
                "access_blocked": False,
                "passkey_ready": passkey_ready,
            },
            status_code=503,
        )

    attempt_status = await asyncio.to_thread(
        login_attempt_status, client_context["client_hash"]
    )
    if not attempt_status["allowed"]:
        if attempt_status.get("block_type") == "manual":
            error = "Cet appareil et ce navigateur ont été bloqués dans le Studio."
            status_code = 403
            access_blocked = True
        else:
            minutes = max(1, (attempt_status["retry_after_seconds"] + 59) // 60)
            error = f"Trop de tentatives. Réessaie dans {minutes} min."
            status_code = 429
            access_blocked = False
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "configured": True,
                "next_path": next_path,
                "error": error,
                "access_blocked": access_blocked,
                "passkey_ready": passkey_ready,
            },
            status_code=status_code,
        )

    if not access_code_matches(access_code):
        failed_status = await asyncio.to_thread(
            record_login_failure, client_context
        )
        if not failed_status["allowed"]:
            error = (
                "Trop de tentatives. Connexion bloquée pendant "
                f"{settings.login_lockout_minutes} min."
            )
            status_code = 429
            background_tasks.add_task(
                send_notification,
                preference="security_lockout",
                title="Accès au Studio temporairement bloqué",
                body=(
                    f"{client_context['device']} • {client_context['browser']} a été "
                    f"bloqué après {settings.login_max_attempts} codes incorrects."
                ),
                url="/?tab=settings",
                tag=f"security-lockout-{client_context['client_hash'][:12]}",
            )
        else:
            remaining = failed_status["remaining_attempts"]
            error = f"Code d’accès incorrect. {remaining} essai(s) restant(s)."
            status_code = 401
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "configured": True,
                "next_path": next_path,
                "error": error,
                "access_blocked": False,
                "passkey_ready": passkey_ready,
            },
            status_code=status_code,
        )

    await asyncio.to_thread(record_login_success, client_context)
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(),
        max_age=session_max_age_seconds(),
        httponly=True,
        secure=settings.studio_cookie_secure,
        samesite="lax",
        path="/",
    )
    background_tasks.add_task(
        send_notification,
        preference="studio_login",
        title="Connexion au Studio",
        body="Une connexion réussie vient d’être effectuée.",
        url="/?tab=settings",
        tag="studio-login",
    )
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.studio_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/session/touch")
async def touch_session():
    return {"ok": True}


# ============================================================
# HOME
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
async def home(
    request: Request
):
    instagram_stored = await stored_credentials_exist()
    try:
        appearance = await asyncio.to_thread(get_appearance_preferences)
    except Exception:
        # Une préférence d’apparence ne doit jamais empêcher le Studio de charger.
        appearance = dict(DEFAULT_APPEARANCE)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "cerebras_ready": bool(
                settings.cerebras_api_key
            ),

            "instagram_ready": bool(
                instagram_stored
                or (
                    settings.instagram_access_token
                    and settings.instagram_user_id
                )
            ),

            "oauth_ready": bool(
                settings.instagram_app_id
                and settings.instagram_app_secret
                and settings.instagram_redirect_uri
            ),

            "model": (
                settings.cerebras_model
            ),

            "max_upload_mb": (
                settings.max_upload_mb
            ),

            "session_idle_seconds": session_max_age_seconds(),

            "publishing_capabilities": publishing_capabilities(),
            "v2_modules": V2_MODULES,
            "trial_reels_enabled": settings.enable_trial_reels,
            "stories_enabled": settings.enable_instagram_stories,
            "mongodb_ready": database_configured(),
            "media_storage_ready": media_storage_configured(),
            "media_storage_label": storage_provider_label(),
            "appearance": appearance,
        },
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():
    return {
        "ok": True,

        "access_control_configured": access_control_configured(),

        "mongodb_configured": database_configured(),

        "media_storage_configured": media_storage_configured(),

        "cerebras_configured": bool(
            settings.cerebras_api_key
        ),

        "instagram_direct_configured": bool(
            settings.instagram_access_token
            and settings.instagram_user_id
        ),

        "facebook_business_login_configured": bool(
            settings.meta_app_id
            and settings.facebook_redirect_uri
        ),
    }


# ============================================================
# IA
# ============================================================

@app.post(
    "/api/ai/caption"
)
async def ai_caption(
    payload: dict
):
    description = str(
        payload.get(
            "description",
            "",
        )
    ).strip()

    if not description:
        return json_error(
            "Décris rapidement la vidéo "
            "avant de générer une caption."
        )

    try:
        result = await generate_caption(
            description=description,

            location=str(
                payload.get(
                    "location",
                    "",
                )
            ).strip(),

            drone=str(
                payload.get(
                    "drone",
                    "",
                )
            ).strip(),

            language=str(
                payload.get(
                    "language",
                    "fr",
                )
            ).strip(),

            tone=str(
                payload.get(
                    "tone",
                    "cinematic",
                )
            ).strip(),

            extra=str(
                payload.get(
                    "extra",
                    "",
                )
            ).strip(),
        )

        return {
            "ok": True,
            "result": result,
            "model": settings.cerebras_model,
        }

    except CerebrasError as exc:
        return json_error(
            str(exc),
            502,
        )


# ============================================================
# UPLOAD
# ============================================================

@app.post(
    "/api/upload"
)
async def upload_media(
    file: UploadFile = File(...)
):
    if not file.filename:
        return json_error(
            "Fichier invalide."
        )

    video_extensions = {".mp4", ".mov", ".m4v"}
    image_extensions = {".jpg", ".jpeg"}
    allowed = video_extensions | image_extensions

    suffix = Path(
        file.filename
    ).suffix.lower()

    if suffix not in allowed:
        return json_error(
            "Format non pris en charge. "
            "Utilise MP4, MOV, M4V, JPG ou JPEG."
        )

    target_name = (
        f"{uuid.uuid4().hex}"
        f"{suffix}"
    )

    target = (
        UPLOAD_DIR
        / target_name
    )

    media_type = "image" if suffix in image_extensions else "video"
    limit_mb = min(settings.max_upload_mb, 8) if media_type == "image" else settings.max_upload_mb
    limit = limit_mb * 1024 * 1024

    total = 0

    try:
        with target.open(
            "wb"
        ) as out:

            while chunk := await file.read(
                1024 * 1024
            ):
                total += len(
                    chunk
                )

                if total > limit:
                    out.close()

                    target.unlink(
                        missing_ok=True
                    )

                    return json_error(
                        "Fichier trop gros. "
                        f"Limite : "
                        f"{limit_mb} Mo.",
                        413,
                    )

                out.write(
                    chunk
                )

    finally:
        await file.close()

    if media_type == "image":
        try:
            is_jpeg = target.read_bytes()[:3] == b"\xff\xd8\xff"
        except OSError:
            is_jpeg = False
        if not is_jpeg:
            target.unlink(missing_ok=True)
            return json_error(
                "La photo n’est pas un véritable fichier JPEG.",
                400,
            )

    public_url = (
        f"{settings.app_base_url}"
        f"/media/{target_name}"
    )

    return {
        "ok": True,
        "url": public_url,
        "filename": target_name,
        "size": total,
        "media_type": media_type,
        "storage": "temporary",
    }


@app.get(
    "/media/{filename}"
)
async def serve_media(
    filename: str
):
    safe_name = os.path.basename(
        filename
    )

    if safe_name != filename:
        raise HTTPException(
            status_code=404
        )

    path = (
        UPLOAD_DIR
        / safe_name
    )

    if (
        not path.exists()
        or not path.is_file()
    ):
        raise HTTPException(
            status_code=404
        )

    return FileResponse(
        path
    )


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


# ============================================================
# PUBLICATION INSTAGRAM
# ============================================================

@app.get("/api/instagram/publishing-limit")
async def instagram_publishing_limit():
    user_id, access_token = await resolve_instagram_credentials()
    if not access_token or not user_id:
        return json_error(
            "Instagram n’est pas configuré pour lire le compteur.",
            503,
        )

    try:
        result = await get_content_publishing_limit(
            user_id=user_id,
            access_token=access_token,
        )
        return {"ok": True, **result}
    except InstagramError as exc:
        return json_error(str(exc), 502)

@app.post(
    "/api/instagram/publish"
)
async def instagram_publish(
    payload: dict
):
    caption = str(
        payload.get(
            "caption",
            "",
        )
    ).strip()

    media_kind = str(payload.get("media_kind", "reel")).strip().lower()
    if media_kind not in {"reel", "photo", "carousel", "story"}:
        return json_error("Type de publication Instagram invalide.")
    if media_kind == "story" and not settings.enable_instagram_stories:
        return json_error("Les Stories sont désactivées sur ce déploiement.", 409)
    try:
        media_items = publication_media_items(payload, media_kind)
    except ValueError as exc:
        return json_error(str(exc))

    publication_mode = str(
        payload.get("publication_mode", "normal")
    ).strip().lower()

    if publication_mode not in {"normal", "trial"}:
        return json_error("Mode de publication Instagram invalide.")

    if media_kind != "reel" and publication_mode != "normal":
        return json_error("Le mode Trial est réservé aux Reels.")

    if publication_mode == "trial" and not settings.enable_trial_reels:
        return json_error(
            "Les Trial Reels sont désactivés sur ce déploiement.",
            409,
        )

    try:
        publication_checks(
            media_kind=media_kind,
            media_items=media_items,
            caption=caption,
            publication_mode=publication_mode,
            workflow="auto_publish",
        )
    except ValueError as exc:
        return json_error(str(exc))
    dedupe_key = publication_fingerprint(
        media_kind=media_kind,
        media_items=media_items,
        caption=caption,
        publication_mode=publication_mode,
        workflow="auto_publish",
    )
    if not await asyncio.to_thread(claim_publication, dedupe_key):
        return json_error(
            "Cette publication identique est déjà en cours ou vient d’être envoyée.",
            409,
        )

    user_id, access_token = await resolve_instagram_credentials()

    if (
        not access_token
        or not user_id
    ):
        await asyncio.to_thread(release_publication_claim, dedupe_key)
        return json_error(
            "Instagram n'est pas encore configuré. "
            "Ajoute INSTAGRAM_ACCESS_TOKEN et "
            "INSTAGRAM_USER_ID dans Render."
        )

    try:
        if media_kind == "photo":
            result = await publish_photo(
                user_id=user_id,
                access_token=access_token,
                image_url=media_items[0]["url"],
                caption=caption,
            )
        elif media_kind == "story":
            result = await publish_story(
                user_id=user_id,
                access_token=access_token,
                media_url=media_items[0]["url"],
                media_type=media_items[0]["media_type"],
            )
        elif media_kind == "carousel":
            result = await publish_carousel(
                user_id=user_id,
                access_token=access_token,
                media_items=media_items,
                caption=caption,
            )
        else:
            result = await publish_reel(
                user_id=user_id,
                access_token=access_token,
                video_url=media_items[0]["url"],
                caption=caption,
                trial=publication_mode == "trial",
            )

        if database_configured():
            library_ids = [
                item["library_id"] for item in media_items if item["library_id"]
            ]
            is_story_video = (
                media_kind == "story" and media_items[0]["media_type"] == "video"
            )
            is_story_image = (
                media_kind == "story" and media_items[0]["media_type"] == "image"
            )
            document = {
                "title": str(payload.get("title", "Publication Instagram"))[:120],
                "media_kind": media_kind,
                "media_items": media_items,
                "library_ids": library_ids,
                "library_id": library_ids[0] if len(library_ids) == 1 else None,
                "video_url": media_items[0]["url"] if media_kind == "reel" or is_story_video else "",
                "image_url": media_items[0]["url"] if media_kind == "photo" or is_story_image else "",
                "thumbnail_url": media_items[0].get("thumbnail_url", ""),
                "caption": caption,
                "hook": str(payload.get("hook", "")),
                "alt_text": str(payload.get("alt_text", "")),
                "publication_mode": publication_mode,
                "workflow": "auto_publish",
                "mute_audio": (
                    bool(payload.get("mute_audio"))
                    if media_kind == "reel" or is_story_video
                    else False
                ),
                "status": "published",
                "dedupe_key": dedupe_key,
                "creation_id": result.get("creation_id"),
                "instagram_media_id": result.get("media_id"),
                "published_at": utc_now(),
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            try:
                await asyncio.to_thread(database().publications.insert_one, document)
            except Exception:
                # Instagram a déjà publié : MongoDB ne doit pas changer ce succès
                # en erreur visible dans le flow V1.
                pass

        return {
            "ok": True,
            **result,
        }

    except InstagramError as exc:
        await asyncio.to_thread(release_publication_claim, dedupe_key)
        return json_error(
            str(exc),
            502,
        )
    except Exception:
        await asyncio.to_thread(release_publication_claim, dedupe_key)
        raise


# ============================================================
# ANCIEN INSTAGRAM OAUTH
# ============================================================

@app.get(
    "/auth/instagram"
)
async def instagram_auth():
    state_payload = {
        "nonce": secrets.token_urlsafe(
            16
        )
    }

    signed_state = serializer.dumps(
        state_payload
    )

    try:
        return RedirectResponse(
            build_authorize_url(
                signed_state
            )
        )

    except InstagramError as exc:
        return JSONResponse(
            {
                "ok": False,
                "error": str(exc),
            },
            status_code=400,
        )


@app.get(
    "/auth/instagram/callback",
    response_class=HTMLResponse,
)
async def instagram_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    if error:
        return HTMLResponse(
            "Connexion Instagram "
            f"annulée/refusée : {error}",
            status_code=400,
        )

    try:
        serializer.loads(
            state
        )

    except BadSignature:
        return HTMLResponse(
            "État OAuth Instagram invalide.",
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            "Code OAuth Instagram manquant.",
            status_code=400,
        )

    try:
        short_token_data = (
            await exchange_code_for_token(
                code
            )
        )

        short_token = str(
            short_token_data.get("access_token", "")
        )

        long_token_data = await exchange_for_long_lived_token(
            short_token
        )

    except InstagramError as exc:
        return HTMLResponse(
            str(exc),
            status_code=502,
        )

    token = long_token_data.get(
        "access_token",
        "",
    )

    user_id = short_token_data.get(
        "user_id",
        "",
    )

    expires_in = int(
        long_token_data.get("expires_in", 0) or 0
    )

    stored_securely = await save_credentials(
        user_id=str(user_id),
        access_token=str(token),
        expires_in=expires_in or 5184000,
    )

    return templates.TemplateResponse(
        request=request,
        name="oauth_success.html",
        context={
            "user_id": user_id,
            "access_token": token,
            "expires_days": round(expires_in / 86400) if expires_in else 60,
            "stored_securely": stored_securely,
        },
    )


# ============================================================
# FACEBOOK / INSTAGRAM ONBOARDING
# ============================================================

@app.get(
    "/auth/facebook"
)
async def facebook_business_auth():
    try:
        url = (
            build_facebook_business_login_url()
        )

        return RedirectResponse(
            url
        )

    except InstagramError as exc:
        return JSONResponse(
            {
                "ok": False,
                "error": str(exc),
            },
            status_code=400,
        )


@app.get(
    "/auth/facebook/callback",
    response_class=HTMLResponse,
)
async def facebook_business_callback():
    """
    Meta renvoie le token dans le fragment URL.

    Exemple :
    #access_token=...
    &long_lived_token=...

    Le fragment n'est pas envoyé au serveur,
    donc on le récupère côté navigateur.
    """

    return HTMLResponse(
        """
<!doctype html>
<html lang="fr">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>Connexion Instagram</title>

<style>

body {
    font-family:
        system-ui,
        -apple-system,
        sans-serif;

    background: #090b10;
    color: white;
    padding: 20px;
}

.card {
    max-width: 700px;
    margin: 30px auto;

    background: #141821;

    border:
        1px solid
        #303746;

    border-radius: 20px;

    padding: 24px;
}

.ok {
    color: #66e3b4;
}

.error {
    color: #ff7c91;
}

.warning {
    color: #ffce73;
}

.value {
    background: #090b10;

    border:
        1px solid
        #303746;

    border-radius: 12px;

    padding: 14px;

    margin-bottom: 20px;

    word-break: break-all;

    user-select: all;
}

a {
    color: #9b8cff;
}

</style>

</head>


<body>

<div class="card">

<h1>
Connexion Instagram
</h1>

<p id="status">
Récupération du token Meta…
</p>

<div id="result"></div>

</div>


<script>

(async () => {

    const status =
        document.getElementById(
            "status"
        );

    const result =
        document.getElementById(
            "result"
        );

    const fragment =
        window.location.hash
        .substring(1);

    const params =
        new URLSearchParams(
            fragment
        );

    const error =
        params.get(
            "error"
        );

    if (error) {

        status.className =
            "error";

        status.textContent =
            "Connexion refusée : "
            + error;

        return;
    }

    /*
     * IMPORTANT :
     *
     * On utilise d'abord access_token.
     * long_lived_token sert uniquement de fallback.
     *
     * Meta peut renvoyer les deux,
     * mais /me/accounts attend un access token
     * OAuth valide.
     */
    const accessToken =
        params.get(
            "access_token"
        );

    const longLivedToken =
        params.get(
            "long_lived_token"
        );

    const token =
        accessToken
        ||
        longLivedToken;

    if (!token) {

        status.className =
            "error";

        status.textContent =
            "Aucun token reçu depuis Meta.";

        const keys =
            Array.from(
                params.keys()
            );

        result.innerHTML =
            `
            <p>
            Paramètres reçus :
            </p>

            <div class="value">
            ${
                keys.length
                ? keys.join(", ")
                : "(aucun)"
            }
            </div>
            `;

        return;
    }

    try {

        const response =
            await fetch(
                "/api/facebook/resolve-instagram",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            {
                                access_token:
                                    token
                            }
                        )
                }
            );

        const data =
            await response.json();

        if (!data.ok) {

            throw new Error(
                data.error
                ||
                "Erreur API inconnue."
            );
        }

        status.className =
            "ok";

        status.textContent =
            "Compte Instagram trouvé ✅";


        result.innerHTML =
            `

            <p>
            <strong>
            Page Facebook
            </strong>
            </p>

            <div class="value">
            ${data.page_name}
            </div>


            <p>
            <strong>
            Page ID
            </strong>
            </p>

            <div class="value">
            ${data.page_id}
            </div>


            <p>
            <strong>
            INSTAGRAM_USER_ID
            </strong>
            </p>

            <div class="value">
            ${data.instagram_user_id}
            </div>


            <p class="warning">
            Le token a été reçu pour cette vérification,
            mais il n’est ni affiché ni journalisé.
            La configuration de production reste dans Render.
            </p>


            <p>
            <a href="/">
            Retour à Instagram Studio
            </a>
            </p>

            `;

    }

    catch (error) {

        status.className =
            "error";

        status.textContent =
            "Erreur : "
            + error.message;
    }

})();

</script>

</body>

</html>
        """
    )


@app.post(
    "/api/facebook/resolve-instagram"
)
async def resolve_instagram_account(
    payload: dict
):
    access_token = str(
        payload.get(
            "access_token",
            "",
        )
    ).strip()

    if not access_token:
        return json_error(
            "Token Meta manquant."
        )

    try:
        account = (
            await get_instagram_business_account(
                access_token
            )
        )

        return {
            "ok": True,
            **account,
        }

    except InstagramError as exc:
        return json_error(
            str(exc),
            502,
        )
