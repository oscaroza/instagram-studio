import json
import re
from typing import Any

import httpx

from app.config import settings


class CerebrasError(RuntimeError):
    pass


CAPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "alt_text": {"type": "string"},
        "hook": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["caption", "hashtags", "alt_text", "hook", "notes"],
    "additionalProperties": False,
}

ANALYTICS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "recommendations": {"type": "array", "items": {"type": "string"}},
        "hook_findings": {"type": "array", "items": {"type": "string"}},
        "timing_findings": {"type": "array", "items": {"type": "string"}},
        "experiments": {"type": "array", "items": {"type": "string"}},
        "cautions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "recommendations",
        "hook_findings",
        "timing_findings",
        "experiments",
        "cautions",
    ],
    "additionalProperties": False,
}

STRICT_JSON_MODELS = {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}


def _response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    if settings.cerebras_model in STRICT_JSON_MODELS:
        return {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        }
    return {"type": "json_object"}


def _reasoning_options() -> dict[str, str]:
    if settings.cerebras_model in STRICT_JSON_MODELS:
        return {"reasoning_effort": "low"}
    return {}


def _groq_error(response: httpx.Response) -> CerebrasError:
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        error = {}
    code = str(error.get("code") or "")
    failed_generation = str(error.get("failed_generation") or "").lower()
    if code == "json_validate_failed" and "max completion tokens" in failed_generation:
        return CerebrasError(
            "Groq n'a pas terminé le JSON avant la limite de sortie. "
            "La requête a été arrêtée sans nouvelle tentative automatique."
        )
    detail = response.text.replace(settings.cerebras_api_key, "[secret redacted]")[:500]
    return CerebrasError(f"Erreur Groq {response.status_code}: {detail}")


def _extract_json(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        raise CerebrasError("L'IA n'a pas renvoyé de JSON valide.")

    text = content.strip().lstrip("\ufeff")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```\s*$", "", text)

    def decode(candidate: str) -> dict[str, Any] | None:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    parsed = decode(text)
    if parsed is not None:
        return parsed

    # Tolère une phrase avant/après le JSON sans utiliser une regex gloutonne,
    # qui échoue dès que la réponse contient plusieurs accolades.
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value

    # Réparation locale des virgules finales, erreur fréquente des modèles.
    repaired = re.sub(r",\s*([}\]])", r"\1", text)
    if repaired != text:
        parsed = decode(repaired)
        if parsed is not None:
            return parsed
        for index, character in enumerate(repaired):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(repaired[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value

    raise CerebrasError("L'IA n'a pas renvoyé de JSON valide.")


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
        "max_completion_tokens": 2048,
        "response_format": _response_format("instagram_caption", CAPTION_SCHEMA),
        **_reasoning_options(),
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
        raise _groq_error(response)

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
Ne présente jamais une corrélation comme une causalité. Si l'échantillon est petit ou les métriques manquent, dis-le clairement. N'invente aucun chiffre ni contenu de publication. Reste concis : une phrase courte par élément de liste."""
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
        "max_completion_tokens": 4096,
        "response_format": _response_format(
            "instagram_performance_analysis", ANALYTICS_SCHEMA
        ),
        **_reasoning_options(),
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
        raise _groq_error(response)
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
