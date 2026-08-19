from app.services.cerebras import _extract_json


def test_extract_plain_json():
    assert _extract_json('{"caption":"hello"}')["caption"] == "hello"


def test_extract_fenced_json():
    assert _extract_json('```json\n{"caption":"hello"}\n```')["caption"] == "hello"
