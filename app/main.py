import os
import secrets
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import settings
from app.services.cerebras import CerebrasError, generate_caption
from app.services.instagram import (
    InstagramError,
    build_authorize_url,
    exchange_code_for_token,
    publish_reel,
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Instagram Studio V1", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
serializer = URLSafeSerializer(settings.app_secret_key, salt="instagram-oauth")


def json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status_code)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "cerebras_ready": bool(settings.cerebras_api_key),
            "instagram_ready": bool(settings.instagram_access_token and settings.instagram_user_id),
            "oauth_ready": bool(settings.instagram_app_id and settings.instagram_app_secret and settings.instagram_redirect_uri),
            "model": settings.cerebras_model,
            "max_upload_mb": settings.max_upload_mb,
        },
    )


@app.get("/health")
async def health():
    return {
        "ok": True,
        "cerebras_configured": bool(settings.cerebras_api_key),
        "instagram_direct_configured": bool(settings.instagram_access_token and settings.instagram_user_id),
        "instagram_oauth_configured": bool(settings.instagram_app_id and settings.instagram_app_secret and settings.instagram_redirect_uri),
    }


@app.post("/api/ai/caption")
async def ai_caption(payload: dict):
    description = str(payload.get("description", "")).strip()
    if not description:
        return json_error("Décris rapidement la vidéo avant de générer une caption.")
    try:
        result = await generate_caption(
            description=description,
            location=str(payload.get("location", "")).strip(),
            drone=str(payload.get("drone", "")).strip(),
            language=str(payload.get("language", "fr")).strip(),
            tone=str(payload.get("tone", "cinematic")).strip(),
            extra=str(payload.get("extra", "")).strip(),
        )
        return {"ok": True, "result": result, "model": settings.cerebras_model}
    except CerebrasError as exc:
        return json_error(str(exc), 502)


@app.post("/api/upload")
async def upload_media(file: UploadFile = File(...)):
    if not file.filename:
        return json_error("Fichier invalide.")
    allowed = {".mp4", ".mov", ".m4v"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed:
        return json_error("Format non pris en charge. Utilise MP4, MOV ou M4V.")

    target_name = f"{uuid.uuid4().hex}{suffix}"
    target = UPLOAD_DIR / target_name
    limit = settings.max_upload_mb * 1024 * 1024
    total = 0
    try:
        with target.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    out.close()
                    target.unlink(missing_ok=True)
                    return json_error(f"Fichier trop gros. Limite: {settings.max_upload_mb} Mo.", 413)
                out.write(chunk)
    finally:
        await file.close()

    public_url = f"{settings.app_base_url}/media/{target_name}"
    return {"ok": True, "url": public_url, "filename": target_name, "size": total}


@app.get("/media/{filename}")
async def serve_media(filename: str):
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        raise HTTPException(status_code=404)
    path = UPLOAD_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.post("/api/instagram/publish")
async def instagram_publish(payload: dict):
    video_url = str(payload.get("video_url", "")).strip()
    caption = str(payload.get("caption", "")).strip()
    if not video_url.startswith(("https://", "http://")):
        return json_error("Une URL vidéo publique est nécessaire.")

    access_token = str(payload.get("access_token") or settings.instagram_access_token).strip()
    user_id = str(payload.get("user_id") or settings.instagram_user_id).strip()
    if not access_token or not user_id:
        return json_error("Instagram n'est pas encore configuré. Ajoute INSTAGRAM_ACCESS_TOKEN et INSTAGRAM_USER_ID dans Render.")

    try:
        result = await publish_reel(
            user_id=user_id,
            access_token=access_token,
            video_url=video_url,
            caption=caption,
        )
        return {"ok": True, **result}
    except InstagramError as exc:
        return json_error(str(exc), 502)


@app.get("/auth/instagram")
async def instagram_auth():
    state_payload = {"nonce": secrets.token_urlsafe(16)}
    signed_state = serializer.dumps(state_payload)
    try:
        return RedirectResponse(build_authorize_url(signed_state))
    except InstagramError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.get("/auth/instagram/callback", response_class=HTMLResponse)
async def instagram_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"Connexion Instagram annulée/refusée: {error}", status_code=400)
    try:
        serializer.loads(state)
    except BadSignature:
        return HTMLResponse("État OAuth invalide.", status_code=400)
    if not code:
        return HTMLResponse("Code OAuth manquant.", status_code=400)
    try:
        token_data = await exchange_code_for_token(code)
    except InstagramError as exc:
        return HTMLResponse(str(exc), status_code=502)

    # V1 deliberately does not persist secrets in Render's ephemeral filesystem.
    # The returned values should be copied into Render environment variables.
    token = token_data.get("access_token", "")
    user_id = token_data.get("user_id", "")
    masked = token[:6] + "…" + token[-4:] if len(token) > 12 else "(reçu)"
    return templates.TemplateResponse(
        request=request,
        name="oauth_success.html",
        context={"user_id": user_id, "masked_token": masked},
    )
