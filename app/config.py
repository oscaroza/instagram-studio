import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    app_secret_key: str = os.getenv("APP_SECRET_KEY", "dev-only-change-me")

    # IA
    # On garde les noms "CEREBRAS_*" pour l'instant même si l'API pointe vers Groq.
    cerebras_api_key: str = os.getenv("CEREBRAS_API_KEY", "")
    cerebras_model: str = os.getenv("CEREBRAS_MODEL", "openai/gpt-oss-20b")
    cerebras_base_url: str = os.getenv(
        "CEREBRAS_BASE_URL",
        "https://api.groq.com/openai/v1",
    ).rstrip("/")

    # Ancien Instagram Login
    instagram_app_id: str = os.getenv("INSTAGRAM_APP_ID", "")
    instagram_app_secret: str = os.getenv("INSTAGRAM_APP_SECRET", "")
    instagram_redirect_uri: str = os.getenv("INSTAGRAM_REDIRECT_URI", "")

    # Facebook Login for Business
    meta_app_id: str = os.getenv("META_APP_ID", "")
    facebook_redirect_uri: str = os.getenv(
        "FACEBOOK_REDIRECT_URI",
        f"{app_base_url}/auth/facebook/callback",
    )

    # API Graph
    instagram_api_base: str = os.getenv(
        "INSTAGRAM_API_BASE",
        "https://graph.facebook.com",
    ).rstrip("/")
    instagram_api_version: str = os.getenv(
        "INSTAGRAM_API_VERSION",
        "v26.0",
    ).strip("/")

    # Connexion directe utilisée pour publier
    instagram_access_token: str = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    instagram_user_id: str = os.getenv("INSTAGRAM_USER_ID", "")

    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "250"))


settings = Settings()