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

CONTENT_IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "objective": {"type": "string"},
                    "why": {"type": "string"},
                    "hook": {"type": "string"},
                    "protocol": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "cta": {"type": "string"},
                    "success_metric": {"type": "string"},
                },
                "required": [
                    "title",
                    "objective",
                    "why",
                    "hook",
                    "protocol",
                    "cta",
                    "success_metric",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["diagnosis", "ideas"],
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
    if code == "json_validate_failed":
        return CerebrasError(
            "Groq n’a pas réussi à terminer le JSON demandé. "
            "Aucune seconde requête automatique n’a été envoyée."
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


def _failed_generation_json(response: httpx.Response) -> dict[str, Any] | None:
    """Recover a complete JSON document rejected only by Groq's strict schema."""
    try:
        error = (response.json() or {}).get("error") or {}
        if str(error.get("code") or "") != "json_validate_failed":
            return None
        failed_generation = error.get("failed_generation")
        return _extract_json(failed_generation)
    except (ValueError, AttributeError, CerebrasError):
        return None


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

    system_prompt = """Tu es un assistant spécialisé dans les captions Instagram pour un créateur de contenu photo et vidéo.
Retourne UNIQUEMENT un objet JSON valide, sans markdown, de la forme :
{
  "caption": "caption prête à poster",
  "hashtags": ["#tag1", "#tag2"],
  "alt_text": "texte alternatif descriptif",
  "hook": "courte accroche",
  "notes": "une phrase très courte expliquant le choix"
}
Règles : naturel, pas de faux faits, pas de hashtags spammy, maximum 12 hashtags, emojis modérés, pas de clickbait mensonger.
Le champ « Appareil de prise de vue » est une contrainte physique et la source de vérité sur la façon dont le contenu a pu être filmé.
- Si l'appareil est un iPhone, un smartphone ou une caméra tenue à la main, n'invente jamais de drone, de vol, de plan aérien, de vue du ciel ou de survol. Ces éléments ne sont permis que si la description ou les consignes indiquent explicitement que l'appareil était placé dans les airs (par exemple dans un avion, sur un drone ou depuis un point de vue en hauteur).
- Si l'appareil est un drone ou un FPV, tu peux employer le vocabulaire aérien uniquement lorsqu'il est cohérent avec la description fournie.
- Ne déduis jamais un angle de vue ou un mouvement de caméra à partir du seul ton demandé.
- Applique ces contraintes à la caption, au hook, au texte alternatif, aux notes et aux hashtags.
- Ne cite pas forcément le nom de l'appareil dans le texte : utilise-le d'abord pour éviter les descriptions impossibles.
Si le lieu ou l'appareil n'est pas fourni, ne l'invente jamais."""

    user_prompt = f"""Crée une proposition pour ce contenu Instagram.
Langue: {language}
Ton: {tone}
Description du contenu: {description}
Lieu: {location or 'non fourni'}
Appareil de prise de vue (contrainte physique): {drone or 'non fourni'}
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


def _compact_analytics_data(dashboard: dict[str, Any]) -> dict[str, Any]:
    growth_series = dashboard.get("growth_series") or []
    return {
        "summary": dashboard.get("summary") or {},
        "period_comparison": dashboard.get("period_comparison") or {},
        "growth": {
            "points": len(growth_series),
            "first": (growth_series or [{}])[0],
            "last": (growth_series or [{}])[-1],
        },
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


async def analyze_instagram_performance(
    dashboard: dict[str, Any],
) -> dict[str, Any]:
    """Analyze anonymized aggregates without sending captions or hook text."""
    if not settings.cerebras_api_key:
        raise CerebrasError("CEREBRAS_API_KEY n'est pas configurée.")

    compact_data = _compact_analytics_data(dashboard)
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
Utilise la comparaison de période et la croissance seulement quand elles contiennent assez de relevés. Ne présente jamais une corrélation comme une causalité. Si l'échantillon est petit ou les métriques manquent, dis-le clairement. N'invente aucun chiffre ni contenu de publication. Reste concis : une phrase courte par élément de liste."""
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


async def generate_growth_content_ideas(
    dashboard: dict[str, Any],
    brief: str = "",
) -> dict[str, Any]:
    """Create actionable ideas without transmitting historical post text."""
    if not settings.cerebras_api_key:
        raise CerebrasError("CEREBRAS_API_KEY n'est pas configurée.")

    cleaned_brief = brief.strip()[:800]
    system_prompt = """Tu es un directeur créatif Instagram spécialisé dans la croissance durable d'un créateur photo, vidéo, drone et FPV.
À partir des statistiques agrégées et du brief fournis, crée EXACTEMENT 3 idées de vidéos différentes et réalisables. Retourne UNIQUEMENT un objet JSON valide conforme au schéma demandé.

But : donner une raison claire de s'abonner, commenter, partager ou enregistrer, sans mendier l'engagement et sans clickbait mensonger.

Règles :
- Chaque idée doit être précise et tournable : hook des 2 premières secondes, CTA naturel et protocole de 5 étapes ordonnées. Chaque étape commence par une plage de temps, puis indique le plan, l'action et, si nécessaire, le texte à l'écran. Exemple : « 0–2 s — Gros plan sur la télécommande • Texte : Tu choisirais lequel ? ».
- Varie les mécanismes : au moins une idée humaine/coulisses ou pédagogique, une idée interactive/comparative et une idée qui conserve la force visuelle du drone sans être seulement un beau montage.
- N'utilise les conclusions statistiques que si les données les soutiennent. Sinon, présente l'idée comme un test.
- Ne promets jamais qu'une idée deviendra virale et ne présente pas une corrélation comme une causalité.
- Respecte strictement le matériel, les lieux, le temps et les contraintes du brief. Un iPhone seul ne produit pas une vue aérienne, sauf situation en hauteur explicitement indiquée.
- Le CTA doit apporter une contrepartie au spectateur : choix à faire, suite annoncée, ressource, comparaison ou utilité à sauvegarder.
- Ne répète pas le diagnostic dans les idées et ne rédige pas de caption complète.
- Reste très concis pour éviter une réponse tronquée : une courte phrase par champ et par étape."""
    user_prompt = (
        "Statistiques anonymisées :\n"
        + json.dumps(_compact_analytics_data(dashboard), ensure_ascii=False)
        + "\n\nBrief et contraintes du créateur :\n"
        + (cleaned_brief or "Aucun brief supplémentaire. Propose des tests réalistes.")
    )
    payload = {
        "model": settings.cerebras_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_completion_tokens": 4096,
        "response_format": _response_format(
            "instagram_growth_content_ideas", CONTENT_IDEAS_SCHEMA
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
        result = _failed_generation_json(response)
        if result is None:
            raise _groq_error(response)
    else:
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise CerebrasError("Réponse Groq inattendue.") from exc
        result = _extract_json(content)

    result["diagnosis"] = str(result.get("diagnosis") or "")[:1200]
    normalized_ideas = []
    for raw_idea in (result.get("ideas") or [])[:3]:
        if not isinstance(raw_idea, dict):
            continue
        idea = {
            "title": str(raw_idea.get("title") or "")[:300],
            "objective": str(raw_idea.get("objective") or "")[:200],
            "why_from_stats": str(
                raw_idea.get("why") or raw_idea.get("why_from_stats") or ""
            )[:700],
            "hook": str(raw_idea.get("hook") or "")[:400],
            "cta": str(raw_idea.get("cta") or "")[:500],
            "success_metric": str(raw_idea.get("success_metric") or "")[:500],
            "concept": "",
            "caption_angle": "",
            "equipment": "Matériel indiqué dans le brief",
            "duration_seconds": 0,
            "on_screen_text": [],
        }
        protocol = raw_idea.get("protocol") or raw_idea.get("shots") or []
        if not isinstance(protocol, list):
            protocol = [protocol]
        idea["shots"] = [str(value)[:500] for value in protocol[:7]]
        normalized_ideas.append(idea)
    if not normalized_ideas:
        raise CerebrasError("Groq n’a renvoyé aucune idée exploitable.")
    result["ideas"] = normalized_ideas
    return result


async def chat_instagram_performance(
    dashboard: dict[str, Any],
    question: str,
) -> str:
    if not settings.cerebras_api_key:
        raise CerebrasError("CEREBRAS_API_KEY n'est pas configurée.")
    cleaned_question = question.strip()[:1200]
    if not cleaned_question:
        raise CerebrasError("Écris une question pour l’assistant.")
    system_prompt = (
        "Tu es le conseiller Instagram personnel d’un créateur drone/FPV. Réponds en "
        "français, de façon concise, concrète et prudente. Utilise uniquement les statistiques "
        "agrégées fournies. Tu n’as accès ni aux vidéos, ni aux textes complets, ni aux tokens "
        "ou secrets. N’invente aucun chiffre et ne transforme pas une corrélation en causalité. "
        "Si les données ne permettent pas de répondre, dis-le."
    )
    payload = {
        "model": settings.cerebras_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "Statistiques anonymisées :\n"
                + json.dumps(_compact_analytics_data(dashboard), ensure_ascii=False)
                + "\n\nQuestion actuelle :\n"
                + cleaned_question,
            },
        ],
        "temperature": 0.3,
        "max_completion_tokens": 1400,
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
    answer = str(content or "").strip()
    if not answer:
        raise CerebrasError("Groq n’a pas renvoyé de réponse.")
    return answer[:5000]
