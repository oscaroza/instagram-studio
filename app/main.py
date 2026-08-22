import asyncio
import os
import secrets
import uuid
from pathlib import Path

from fastapi import (
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
    publish_reel,
)
from app.routes.v2 import router as v2_router
from app.services.cloudinary_media import (
    CloudinaryMediaError,
    cloudinary_configured,
    delete_video as delete_cloudinary_video,
    upload_video as upload_video_to_cloudinary,
)
from app.services.database import (
    database,
    database_configured,
    serialize_document,
    utc_now,
)
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.token_store import (
    resolve_instagram_credentials,
    save_credentials,
    stored_credentials_exist,
)
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
}


@app.middleware("http")
async def require_studio_session(request: Request, call_next):
    path = request.url.path
    public = (
        path in PUBLIC_PATHS
        or path.startswith("/static/")
        or path.startswith("/media/")
    )

    if public or request_is_authenticated(request):
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

    if not path.startswith("/static/") and not path.startswith("/media/"):
        response.headers["Cache-Control"] = "no-store"
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
    if request_is_authenticated(request):
        return RedirectResponse(safe_next_path(next), status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "configured": access_control_configured(),
            "next_path": safe_next_path(next),
            "error": "",
        },
    )


@app.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    access_code: str = Form(...),
    next: str = Form("/"),
):
    next_path = safe_next_path(next)

    if not access_control_configured():
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "configured": False,
                "next_path": next_path,
                "error": "STUDIO_ACCESS_CODE doit être configuré côté serveur.",
            },
            status_code=503,
        )

    if not access_code_matches(access_code):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "configured": True,
                "next_path": next_path,
                "error": "Code d’accès incorrect.",
            },
            status_code=401,
        )

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

            "publishing_capabilities": publishing_capabilities(),
            "v2_modules": V2_MODULES,
            "trial_reels_enabled": settings.enable_trial_reels,
            "mongodb_ready": database_configured(),
            "cloudinary_ready": cloudinary_configured(),
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

        "cloudinary_configured": cloudinary_configured(),

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

    allowed = {
        ".mp4",
        ".mov",
        ".m4v",
    }

    suffix = Path(
        file.filename
    ).suffix.lower()

    if suffix not in allowed:
        return json_error(
            "Format non pris en charge. "
            "Utilise MP4, MOV ou M4V."
        )

    target_name = (
        f"{uuid.uuid4().hex}"
        f"{suffix}"
    )

    target = (
        UPLOAD_DIR
        / target_name
    )

    limit = (
        settings.max_upload_mb
        * 1024
        * 1024
    )

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
                        f"{settings.max_upload_mb} Mo.",
                        413,
                    )

                out.write(
                    chunk
                )

    finally:
        await file.close()

    public_url = (
        f"{settings.app_base_url}"
        f"/media/{target_name}"
    )

    if database_configured() and cloudinary_configured():
        cloud_media = None
        try:
            cloud_media = await asyncio.to_thread(
                upload_video_to_cloudinary,
                target,
                file.filename,
            )
            document = {
                "cloudinary_public_id": cloud_media["public_id"],
                "secure_url": cloud_media["secure_url"],
                "thumbnail_url": cloud_media["thumbnail_url"],
                "bytes": cloud_media["bytes"],
                "duration": cloud_media["duration"],
                "format": cloud_media["format"],
                "width": cloud_media["width"],
                "height": cloud_media["height"],
                "original_filename": cloud_media["original_filename"],
                "created_at": utc_now(),
            }
            insert_result = await asyncio.to_thread(
                database().media.insert_one,
                document,
            )
            target.unlink(missing_ok=True)
            document["_id"] = insert_result.inserted_id
            return {
                "ok": True,
                "url": cloud_media["secure_url"],
                "filename": file.filename,
                "size": cloud_media["bytes"],
                "storage": "cloudinary",
                "media": serialize_document(document),
            }
        except CloudinaryMediaError as exc:
            target.unlink(missing_ok=True)
            return json_error(str(exc), 502)
        except Exception:
            if cloud_media and cloud_media.get("public_id"):
                try:
                    await asyncio.to_thread(
                        delete_cloudinary_video,
                        cloud_media["public_id"],
                    )
                except Exception:
                    pass
            target.unlink(missing_ok=True)
            return json_error(
                "La vidéo a été envoyée, mais son enregistrement MongoDB a échoué.",
                503,
            )

    return {
        "ok": True,
        "url": public_url,
        "filename": target_name,
        "size": total,
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
    video_url = str(
        payload.get(
            "video_url",
            "",
        )
    ).strip()

    caption = str(
        payload.get(
            "caption",
            "",
        )
    ).strip()

    publication_mode = str(
        payload.get("publication_mode", "normal")
    ).strip().lower()

    if publication_mode not in {"normal", "trial"}:
        return json_error("Mode de publication Instagram invalide.")

    if publication_mode == "trial" and not settings.enable_trial_reels:
        return json_error(
            "Les Trial Reels sont désactivés sur ce déploiement.",
            409,
        )

    if not video_url.startswith(
        (
            "https://",
            "http://",
        )
    ):
        return json_error(
            "Une URL vidéo publique est nécessaire."
        )

    user_id, access_token = await resolve_instagram_credentials()

    if (
        not access_token
        or not user_id
    ):
        return json_error(
            "Instagram n'est pas encore configuré. "
            "Ajoute INSTAGRAM_ACCESS_TOKEN et "
            "INSTAGRAM_USER_ID dans Render."
        )

    try:
        result = await publish_reel(
            user_id=user_id,
            access_token=access_token,
            video_url=video_url,
            caption=caption,
            trial=publication_mode == "trial",
        )

        if database_configured():
            document = {
                "title": str(payload.get("title", "Publication Instagram"))[:120],
                "library_id": str(payload.get("library_id", "")) or None,
                "video_url": video_url,
                "thumbnail_url": str(payload.get("thumbnail_url", "")),
                "caption": caption,
                "hook": str(payload.get("hook", "")),
                "alt_text": str(payload.get("alt_text", "")),
                "publication_mode": publication_mode,
                "workflow": "auto_publish",
                "status": "published",
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
        return json_error(
            str(exc),
            502,
        )


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
