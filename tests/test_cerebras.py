import json

import httpx
import pytest

from app.services.cerebras import CerebrasError, _extract_json, _groq_error


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
