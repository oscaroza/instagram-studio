import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from app.services import cerebras
from app.services.cerebras import CerebrasError, _extract_json, _groq_error


class FakeCaptionResponse:
    status_code = 200

    @staticmethod
    def json():
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "caption": "Une scène filmée au bord de l'eau.",
                                "hashtags": ["mobilevideo"],
                                "alt_text": "La mer filmée depuis le rivage.",
                                "hook": "Au bord de l'eau",
                                "notes": "Angle cohérent avec un téléphone.",
                            }
                        )
                    }
                }
            ]
        }


class FakeCaptionClient:
    last_payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, *args, **kwargs):
        type(self).last_payload = kwargs["json"]
        return FakeCaptionResponse()


def test_extract_plain_json():
    assert _extract_json('{"caption":"hello"}')["caption"] == "hello"


def test_extract_fenced_json():
    assert _extract_json('```json\n{"caption":"hello"}\n```')["caption"] == "hello"


def test_extract_json_surrounded_by_explanation():
    result = _extract_json('Voici le résultat :\n{"summary":"ok"}\nFin de réponse.')
    assert result == {"summary": "ok"}


def test_extract_json_repairs_trailing_commas_locally():
    result = _extract_json('{"summary":"ok","recommendations":["test",],}')
    assert result == {"summary": "ok", "recommendations": ["test"]}


def test_extract_json_rejects_non_object_content():
    with pytest.raises(CerebrasError, match="JSON valide"):
        _extract_json("aucun objet dans cette réponse")


def test_completion_limit_error_is_short_and_does_not_trigger_a_retry():
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        content=json.dumps(
            {
                "error": {
                    "code": "json_validate_failed",
                    "failed_generation": "max completion tokens reached before generating a valid document",
                }
            }
        ).encode(),
    )

    error = _groq_error(response)

    assert "limite de sortie" in str(error)
    assert "sans nouvelle tentative" in str(error)


def test_caption_device_is_a_physical_constraint(monkeypatch):
    monkeypatch.setattr(
        cerebras,
        "settings",
        replace(cerebras.settings, cerebras_api_key="test-key"),
    )
    monkeypatch.setattr(cerebras.httpx, "AsyncClient", FakeCaptionClient)

    result = asyncio.run(
        cerebras.generate_caption(
            description="La mer au coucher du soleil",
            drone="iPhone 16 Pro",
        )
    )

    messages = FakeCaptionClient.last_payload["messages"]
    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]
    assert "contrainte physique" in system_prompt
    assert "n'invente jamais de drone" in system_prompt
    assert "vue du ciel" in system_prompt
    assert "Appareil de prise de vue (contrainte physique): iPhone 16 Pro" in user_prompt
    assert result["hashtags"] == ["#mobilevideo"]
