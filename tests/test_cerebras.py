import pytest

from app.services.cerebras import CerebrasError, _extract_json


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
