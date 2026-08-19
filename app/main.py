import os
import secrets
import uuid
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
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
    exchange_facebook_code_for_token,
    get_instagram_business_account,
    publish_reel,
)


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
    title="Instagram Studio V1",
    version="1.2.0",
)


app.mount(
    "/static",
    StaticFiles(
        directory=BASE_DIR / "static"
    ),
    name="static",
)


templates = Jinja2Templates(
    directory=BASE_DIR
    / "templates"
)


serializer = URLSafeSerializer(
    settings.app_secret_key,
    salt="instagram-oauth",
)


facebook_serializer = URLSafeSerializer(
    settings.app_secret_key,
    salt="facebook-business-oauth",
)


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
# HOME
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
async def home(
    request: Request
):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "cerebras_ready": bool(
                settings.cerebras_api_key
            ),

            "instagram_ready": bool(
                settings.instagram_access_token
                and settings.instagram_user_id
            ),

            "oauth_ready": bool(
                settings.meta_app_id
                and settings.facebook_config_id
                and settings.facebook_redirect_uri
            ),

            "model": (
                settings.cerebras_model
            ),

            "max_upload_mb": (
                settings.max_upload_mb
            ),
        },
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():
    return {
        "ok": True,

        "cerebras_configured": bool(
            settings.cerebras_api_key
        ),

        "instagram_direct_configured": bool(
            settings.instagram_access_token
            and settings.instagram_user_id
        ),

        "facebook_business_login_configured": bool(
            settings.meta_app_id
            and settings.facebook_config_id
            and settings.facebook_redirect_uri
        ),

        "meta_app_secret_configured": bool(
            os.getenv(
                "META_APP_SECRET",
                "",
            )
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


# ============================================================
# PUBLICATION INSTAGRAM
# ============================================================

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

    if not video_url.startswith(
        (
            "https://",
            "http://",
        )
    ):
        return json_error(
            "Une URL vidéo publique "
            "est nécessaire."
        )

    access_token = str(
        payload.get(
            "access_token"
        )
        or settings.instagram_access_token
    ).strip()

    user_id = str(
        payload.get(
            "user_id"
        )
        or settings.instagram_user_id
    ).strip()

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
        )

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

    signed_state = (
        serializer.dumps(
            state_payload
        )
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
        token_data = (
            await exchange_code_for_token(
                code
            )
        )

    except InstagramError as exc:
        return HTMLResponse(
            str(exc),
            status_code=502,
        )

    token = token_data.get(
        "access_token",
        "",
    )

    user_id = token_data.get(
        "user_id",
        "",
    )

    masked = (
        token[:6]
        + "…"
        + token[-4:]
        if len(token) > 12
        else "(reçu)"
    )

    return templates.TemplateResponse(
        request=request,
        name="oauth_success.html",
        context={
            "user_id": user_id,
            "masked_token": masked,
        },
    )


# ============================================================
# FACEBOOK LOGIN FOR BUSINESS
# ============================================================

@app.get(
    "/auth/facebook"
)
async def facebook_business_auth():
    """
    Début du flow Facebook Login for Business.
    """

    state_payload = {
        "nonce": secrets.token_urlsafe(
            24
        )
    }

    state = (
        facebook_serializer.dumps(
            state_payload
        )
    )

    try:
        url = (
            build_facebook_business_login_url(
                state
            )
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
async def facebook_business_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_reason: str = "",
    error_description: str = "",
):
    """
    Facebook renvoie maintenant :

    /auth/facebook/callback?code=...

    Le serveur échange ensuite ce code
    contre un access token Meta.
    """

    if error:
        message = (
            error_description
            or error_reason
            or error
        )

        return HTMLResponse(
            "Connexion Facebook/Meta "
            f"annulée ou refusée : {message}",
            status_code=400,
        )

    if not state:
        return HTMLResponse(
            "Paramètre state Meta manquant.",
            status_code=400,
        )

    try:
        facebook_serializer.loads(
            state
        )

    except BadSignature:
        return HTMLResponse(
            "État OAuth Meta invalide.",
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            "Meta n'a pas renvoyé de code OAuth.",
            status_code=400,
        )

    try:
        # 1. Code → access token
        token_data = (
            await exchange_facebook_code_for_token(
                code
            )
        )

        access_token = str(
            token_data.get(
                "access_token",
                "",
            )
        ).strip()

        if not access_token:
            raise InstagramError(
                "Token Meta vide après échange OAuth."
            )

        # 2. Token → Page Facebook
        #             + Instagram Business ID
        account = (
            await get_instagram_business_account(
                access_token
            )
        )

    except InstagramError as exc:
        return HTMLResponse(
            f"""
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>Erreur Meta</title>

<style>
body {{
    font-family: system-ui, -apple-system, sans-serif;
    background: #090b10;
    color: white;
    padding: 20px;
}}

.card {{
    max-width: 700px;
    margin: 40px auto;
    background: #141821;
    border: 1px solid #303746;
    border-radius: 20px;
    padding: 24px;
}}

.error {{
    color: #ff7c91;
    word-break: break-word;
}}
</style>
</head>

<body>
<div class="card">

<h1>Connexion Meta ❌</h1>

<p class="error">
{str(exc)}
</p>

<p>
Tu peux revenir sur Instagram Studio
et réessayer après correction.
</p>

<a href="/"
   style="color:#9b8cff;">
Retour au Studio
</a>

</div>
</body>
</html>
            """,
            status_code=502,
        )

    instagram_user_id = (
        account[
            "instagram_user_id"
        ]
    )

    page_name = (
        account.get(
            "page_name",
            "",
        )
    )

    page_id = (
        account.get(
            "page_id",
            "",
        )
    )

    # On n'enregistre volontairement PAS
    # le token sur le filesystem Render.
    #
    # Le token doit être copié
    # manuellement dans Environment.
    return HTMLResponse(
        f"""
<!doctype html>
<html lang="fr">

<head>
<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>Instagram connecté</title>

<style>

body {{
    font-family:
        system-ui,
        -apple-system,
        sans-serif;

    background: #090b10;
    color: white;
    padding: 20px;
}}

.card {{
    max-width: 700px;
    margin: 30px auto;

    background: #141821;

    border:
        1px solid
        #303746;

    border-radius: 20px;

    padding: 24px;
}}

.ok {{
    color: #66e3b4;
}}

.value {{
    background: #090b10;

    border:
        1px solid
        #303746;

    border-radius: 12px;

    padding: 14px;

    margin-bottom: 20px;

    word-break: break-all;

    user-select: all;
}}

.warning {{
    color: #ffce73;
}}

a {{
    color: #9b8cff;
}}

</style>

</head>


<body>

<div class="card">

<h1 class="ok">
Instagram connecté ✅
</h1>


<p>
<strong>Page Facebook :</strong><br>
{page_name}
</p>


<p>
<strong>Page ID :</strong>
</p>

<div class="value">
{page_id}
</div>


<p>
<strong>
INSTAGRAM_USER_ID
</strong>
</p>

<div class="value">
{instagram_user_id}
</div>


<p>
<strong>
INSTAGRAM_ACCESS_TOKEN
</strong>
</p>

<div class="value">
{access_token}
</div>


<p class="warning">
⚠️ Ce token est secret.
Ne le partage pas et ne le mets pas sur GitHub.
</p>


<p>
Dans Render → Environment,
ajoute maintenant :
</p>

<div class="value">
INSTAGRAM_USER_ID
</div>

<p>
avec la valeur affichée plus haut.
</p>


<div class="value">
INSTAGRAM_ACCESS_TOKEN
</div>

<p>
avec le token affiché plus haut.
</p>


<p>
Ensuite fais Save Changes
et attends le redéploiement.
</p>


<a href="/">
Retour à Instagram Studio
</a>

</div>

</body>
</html>
        """
    )