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
        detail = response.text.replace(
            settings.cerebras_api_key,
            "[secret redacted]",
        )[:500]
        raise CerebrasError(f"Erreur Groq {response.status_code}: {detail}")

    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise CerebrasError("Réponse Groq inattendue.") from exc

    result = _extract_json(content)
    hashtags = result.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = hashtags.split()
    result["hashtags"] = [h if h.startswith("#") else f"#{h}" for h in hashtags][:12]
    return result


def _hook_features(hook: str) -> dict[str, Any]:
    cleaned = hook.strip()
    return {
        "characters": len(cleaned),
        "words": len(cleaned.split()),
        "contains_question": "?" in cleaned,
        "contains_number": any(character.isdigit() for character in cleaned),
        "contains_emoji": any(ord(character) > 10000 for character in cleaned),
    }


async def analyze_instagram_performance(
    dashboard: dict[str, Any],
) -> dict[str, Any]:
    """Analyze anonymized aggregates without sending captions or hook text."""
    if not settings.cerebras_api_key:
        raise CerebrasError("CEREBRAS_API_KEY n'est pas configurée.")

    compact_data = {
        "summary": dashboard.get("summary") or {},
        "best_times": (dashboard.get("best_times") or [])[:8],
        "top_posts": [
            {
                "media_kind": item.get("media_kind", ""),
                "views": item.get("views", 0),
                "reach": item.get("reach", 0),
                "interactions": item.get("interactions", 0),
                "engagement_rate": round(float(item.get("engagement_rate", 0)), 3),
                "hook_features": _hook_features(str(item.get("hook", ""))),
            }
            for item in (dashboard.get("top_posts") or [])[:25]
        ],
    }
    system_prompt = """Tu es l'assistant stratégique d'un créateur Instagram drone/FPV.
Analyse uniquement les données agrégées fournies. Retourne UNIQUEMENT un objet JSON valide :
{
  "summary": "bilan clair en 2 ou 3 phrases",
  "recommendations": ["3 à 6 actions concrètes et testables"],
  "hook_findings": ["constats sur les caractéristiques anonymisées des hooks"],
  "timing_findings": ["constats sur les jours et heures, en citant la taille des échantillons"],
  "experiments": ["2 ou 3 tests simples pour les prochaines publications"],
  "cautions": ["limites importantes des données"]
}
Ne présente jamais une corrélation comme une causalité. Si l'échantillon est petit ou les métriques manquent, dis-le clairement. N'invente aucun chiffre ni contenu de publication."""
    payload = {
        "model": settings.cerebras_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "Analyse ces performances Instagram :\n"
                + json.dumps(compact_data, ensure_ascii=False),
            },
        ],
        "temperature": 0.25,
        "max_completion_tokens": 1100,
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
        detail = response.text.replace(settings.cerebras_api_key, "[secret redacted]")[:500]
        raise CerebrasError(f"Erreur Groq {response.status_code}: {detail}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise CerebrasError("Réponse Groq inattendue.") from exc

    result = _extract_json(content)
    result["summary"] = str(result.get("summary", ""))[:1800]
    for key in (
        "recommendations",
        "hook_findings",
        "timing_findings",
        "experiments",
        "cautions",
    ):
        values = result.get(key) or []
        if not isinstance(values, list):
            values = [values]
        result[key] = [str(value)[:600] for value in values[:8]]
    return result
