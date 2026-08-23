import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # ------------------------------------------------------------
    # Application
    # ------------------------------------------------------------

    app_base_url: str = os.getenv(
        "APP_BASE_URL",
        "http://localhost:8000",
    ).rstrip("/")

    app_secret_key: str = os.getenv(
        "APP_SECRET_KEY",
        "dev-only-change-me",
    )

    studio_access_code: str = os.getenv(
        "STUDIO_ACCESS_CODE",
        "",
    )

    studio_session_hours: int = int(
        os.getenv(
            "STUDIO_SESSION_HOURS",
            "24",
        )
    )

    studio_idle_minutes: int = int(
        os.getenv(
            "STUDIO_IDLE_MINUTES",
            "10",
        )
    )

    studio_cookie_secure: bool = os.getenv(
        "STUDIO_COOKIE_SECURE",
        "true" if app_base_url.startswith("https://") else "false",
    ).lower() in {"1", "true", "yes", "on"}

    login_max_attempts: int = max(1, int(os.getenv("LOGIN_MAX_ATTEMPTS", "5")))
    login_window_minutes: int = max(
        1, int(os.getenv("LOGIN_WINDOW_MINUTES", "15"))
    )
    login_lockout_minutes: int = max(
        1, int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))
    )

    # ------------------------------------------------------------
    # IA
    # ------------------------------------------------------------
    # On garde volontairement les noms CEREBRAS_* pour l'instant,
    # même si ces variables pointent maintenant vers Groq.
    # On fera le ménage plus tard sur ordinateur.
    # ------------------------------------------------------------

    cerebras_api_key: str = os.getenv(
        "CEREBRAS_API_KEY",
        "",
    )

    cerebras_model: str = os.getenv(
        "CEREBRAS_MODEL",
        "openai/gpt-oss-20b",
    )

    cerebras_base_url: str = os.getenv(
        "CEREBRAS_BASE_URL",
        "https://api.groq.com/openai/v1",
    ).rstrip("/")

    # ------------------------------------------------------------
    # Ancien Instagram Login
    # ------------------------------------------------------------
    # On le garde pour l'instant pour ne rien casser.
    # ------------------------------------------------------------

    instagram_app_id: str = os.getenv(
        "INSTAGRAM_APP_ID",
        "",
    )

    instagram_app_secret: str = os.getenv(
        "INSTAGRAM_APP_SECRET",
        "",
    )

    instagram_redirect_uri: str = os.getenv(
        "INSTAGRAM_REDIRECT_URI",
        "",
    )

    # ------------------------------------------------------------
    # Facebook Login for Business
    # ------------------------------------------------------------

    meta_app_id: str = os.getenv(
        "META_APP_ID",
        "",
    )

    facebook_config_id: str = os.getenv(
        "FACEBOOK_CONFIG_ID",
        "",
    )

    facebook_redirect_uri: str = os.getenv(
        "FACEBOOK_REDIRECT_URI",
        f"{app_base_url}/auth/facebook/callback",
    )

    # ------------------------------------------------------------
    # Meta Graph API
    # ------------------------------------------------------------

    instagram_api_base: str = os.getenv(
        "INSTAGRAM_API_BASE",
        "https://graph.facebook.com",
    ).rstrip("/")

    instagram_api_version: str = os.getenv(
        "INSTAGRAM_API_VERSION",
        "v26.0",
    ).strip("/")

    # ------------------------------------------------------------
    # Identifiants finaux utilisés pour publier
    # ------------------------------------------------------------

    instagram_access_token: str = os.getenv(
        "INSTAGRAM_ACCESS_TOKEN",
        "",
    )

    instagram_user_id: str = os.getenv(
        "INSTAGRAM_USER_ID",
        "",
    )

    enable_trial_reels: bool = os.getenv(
        "ENABLE_TRIAL_REELS",
        "true",
    ).lower() in {"1", "true", "yes", "on"}

    # ------------------------------------------------------------
    # V2 persistence and media storage
    # ------------------------------------------------------------

    mongodb_uri: str = os.getenv("MONGODB_URI", "")
    mongodb_database: str = os.getenv(
        "MONGODB_DATABASE",
        "instagram_studio",
    )

    cloudinary_cloud_name: str = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    cloudinary_api_key: str = os.getenv("CLOUDINARY_API_KEY", "")
    cloudinary_api_secret: str = os.getenv("CLOUDINARY_API_SECRET", "")
    cloudinary_folder: str = os.getenv(
        "CLOUDINARY_FOLDER",
        "instagram-studio",
    ).strip("/")

    # ------------------------------------------------------------
    # PWA Web Push
    # ------------------------------------------------------------

    vapid_public_key: str = os.getenv("VAPID_PUBLIC_KEY", "")
    vapid_private_key: str = os.getenv("VAPID_PRIVATE_KEY", "")
    vapid_subject: str = os.getenv(
        "VAPID_SUBJECT",
        "mailto:admin@example.com",
    )

    scheduler_interval_seconds: int = int(
        os.getenv("SCHEDULER_INTERVAL_SECONDS", "30")
    )

    analytics_max_media: int = int(
        os.getenv("ANALYTICS_MAX_MEDIA", "100")
    )

    # ------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------

    max_upload_mb: int = int(
        os.getenv(
            "MAX_UPLOAD_MB",
            "250",
        )
    )


settings = Settings()
