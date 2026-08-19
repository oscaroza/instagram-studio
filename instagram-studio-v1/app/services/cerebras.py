import json
import re
from typing import Any

import httpx

from app.config import settings


class CerebrasError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\\s*", "", text)
        text = re.sub(r"\\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise CerebrasError("L'IA n'a pas renvoyé de JSON valide.")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise CerebrasError("Impossible de parser la réponse de l'IA.") from exc


async def generate_caption(
    *,
    description: str,
    location: str = "",
    drone: str = "",
    language: str = "fr",
    tone: str = "cinematic",
    extra: str = "",
) -> dict[str, Any]:
    if not settings.cerebras_api_key:
        raise CerebrasError("CEREBRAS_API_KEY n'est pas configurée.")

    system_prompt = """Tu es un assistant spécialisé dans les captions Instagram pour un créateur de contenu drone/FPV.
Retourne UNIQUEMENT un objet JSON valide, sans markdown, de la forme :
{
  "caption": "caption prête à poster",
  "hashtags": ["#tag1", "#tag2"],
  "alt_text": "texte alternatif descriptif",
  "hook": "courte accroche",
  "notes": "une phrase très courte expliquant le choix"
}
Règles : naturel, pas de faux faits, pas de hashtags spammy, maximum 12 hashtags, emojis modérés, pas de clickbait mensonger.
Si le lieu ou le drone n'est pas fourni, ne l'invente jamais."""

    user_prompt = f"""Crée une proposition pour ce contenu Instagram.
Langue: {language}
Ton: {tone}
Description du contenu: {description}
Lieu: {location or 'non fourni'}
Drone/caméra: {drone or 'non fourni'}
Consignes supplémentaires: {extra or 'aucune'}
"""

    payload = {
        "model": settings.cerebras_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_completion_tokens": 700,
    }
    headers = {
        "Authorization": f"Bearer {settings.cerebras_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            f"{settings.cerebras_base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
    if response.status_code >= 400:
        detail = response.text[:500]
        raise CerebrasError(f"Erreur Cerebras {response.status_code}: {detail}")

    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise CerebrasError("Réponse Cerebras inattendue.") from exc

    result = _extract_json(content)
    hashtags = result.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = hashtags.split()
    result["hashtags"] = [h if h.startswith("#") else f"#{h}" for h in hashtags][:12]
    return result
