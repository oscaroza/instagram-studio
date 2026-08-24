from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    meta_supported: bool
    app_enabled: bool
    status: str


def publishing_capabilities() -> tuple[Capability, ...]:
    return (
        Capability(
            key="reel",
            label="Reel normal",
            meta_supported=True,
            app_enabled=True,
            status="Disponible — flow V1 conservé",
        ),
        Capability(
            key="trial_reel",
            label="Trial Reel",
            meta_supported=True,
            app_enabled=settings.enable_trial_reels,
            status=(
                "Disponible"
                if settings.enable_trial_reels
                else "Supporté par Meta, activation contrôlée requise"
            ),
        ),
        Capability(
            key="photo",
            label="Photo JPEG",
            meta_supported=True,
            app_enabled=True,
            status="Disponible — immédiat et programmé",
        ),
        Capability(
            key="carousel",
            label="Carrousel photo/vidéo",
            meta_supported=True,
            app_enabled=True,
            status="Disponible — 2 à 10 photos ou vidéos",
        ),
        Capability(
            key="story",
            label="Story photo/vidéo",
            meta_supported=True,
            app_enabled=settings.enable_instagram_stories,
            status=(
                "Disponible — immédiate et programmée"
                if settings.enable_instagram_stories
                else "Désactivée pour ce compte via ENABLE_INSTAGRAM_STORIES"
            ),
        ),
    )


V2_MODULES = (
    ("calendar", "Calendrier", "Actif avec programmation côté serveur"),
    ("library", "Bibliothèque", "Active avec Cloudflare R2, Cloudinary historique et MongoDB"),
    ("drafts", "Brouillons", "V1 locale conservée, persistance serveur à venir"),
    ("notifications", "Notifications", "Web Push PWA avec préférences"),
)
