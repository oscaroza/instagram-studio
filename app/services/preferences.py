import re
from copy import deepcopy
from typing import Any

from app.services.database import database, database_configured, utc_now


APPEARANCE_ID = "studio-appearance"
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_APPEARANCE: dict[str, Any] = {
    "accent": "#9f7aea",
    "accent_text": "#08090d",
    "background": "#08090d",
    "surface": "#11131a",
    "text": "#f6f7fb",
    "density": "comfortable",
    "radius": 18,
}


def _accent_text_color(hex_color: str) -> str:
    red, green, blue = (
        int(hex_color[index : index + 2], 16) / 255
        for index in (1, 3, 5)
    )
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return "#08090d" if luminance >= 0.48 else "#ffffff"


def normalize_appearance(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    normalized = deepcopy(DEFAULT_APPEARANCE)

    for field in ("accent", "background", "surface", "text"):
        value = str(source.get(field, normalized[field])).strip().lower()
        if not HEX_COLOR.fullmatch(value):
            raise ValueError(f"La couleur {field} est invalide.")
        normalized[field] = value
    normalized["accent_text"] = _accent_text_color(normalized["accent"])

    density = str(source.get("density", normalized["density"])).strip().lower()
    if density not in {"comfortable", "compact"}:
        raise ValueError("La densité d’affichage est invalide.")
    normalized["density"] = density

    try:
        radius = int(source.get("radius", normalized["radius"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("L’arrondi des cartes est invalide.") from exc
    if radius < 10 or radius > 24:
        raise ValueError("L’arrondi doit être compris entre 10 et 24 pixels.")
    normalized["radius"] = radius
    return normalized


def get_appearance_preferences() -> dict[str, Any]:
    if not database_configured():
        return deepcopy(DEFAULT_APPEARANCE)
    document = database().studio_preferences.find_one({"_id": APPEARANCE_ID})
    if not document:
        return deepcopy(DEFAULT_APPEARANCE)
    try:
        return normalize_appearance(document)
    except ValueError:
        return deepcopy(DEFAULT_APPEARANCE)


def save_appearance_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    if not database_configured():
        raise RuntimeError("MongoDB est nécessaire pour synchroniser la personnalisation.")
    appearance = normalize_appearance(payload)
    database().studio_preferences.replace_one(
        {"_id": APPEARANCE_ID},
        {"_id": APPEARANCE_ID, **appearance, "updated_at": utc_now()},
        upsert=True,
    )
    return appearance


def reset_appearance_preferences() -> dict[str, Any]:
    if not database_configured():
        raise RuntimeError("MongoDB est nécessaire pour synchroniser la personnalisation.")
    database().studio_preferences.delete_one({"_id": APPEARANCE_ID})
    return deepcopy(DEFAULT_APPEARANCE)
