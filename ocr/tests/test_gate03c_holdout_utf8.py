import hashlib
import json

import pytest
from ocr.schemas import SafeError
from ocr.tests.test_anonymization_runtime_safety import offline_helpers


@pytest.mark.parametrize(
    "text", ["plain ASCII", "München, Grüße!", "naïve café 🌍", "漢字🙂aéß", "नमस्ते दुनिया"]
)
def test_full_and_every_truncation_boundary_roundtrip(text):
    tokenizer = offline_helpers().OfflineTokenizer("absent-cache")
    tokenizer._attempted = True
    tokens = tokenizer.encode(text)
    assert tokenizer.decode(tokens) == text
    for direction in ("prefix", "suffix"):
        for limit in range(len(tokens) + 1):
            shortened = tokenizer.truncate_tokens(tokens, limit, keep=direction)
            decoded = tokenizer.decode(shortened)
            assert len(shortened) <= limit
            assert text.startswith(decoded) if direction == "prefix" else text.endswith(decoded)
            assert tokenizer.decode(tokenizer.encode(decoded)) == decoded
            assert "\ufffd" not in decoded


def test_direct_partial_utf8_decode_fails_instead_of_silently_losing_character():
    tokenizer = offline_helpers().OfflineTokenizer("absent-cache")
    tokenizer._attempted = True
    with pytest.raises(SafeError, match="tokenizer_invalid_or_truncated_utf8"):
        tokenizer.decode(tokenizer.encode("é")[:2])
    with pytest.raises(SafeError):
        tokenizer.decode([256, 999, 257])


def test_frozen_holdout_integrity_and_control_unchanged():
    from pathlib import Path

    from ocr.anonymization.recognizer import DEFAULT_PROMPTS

    root = Path(__file__).parents[1] / "anonymization/fixtures/holdout03c"
    freeze = json.loads((root / "freeze.json").read_text())
    raw = (root / "cases.json").read_bytes()
    cases = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == freeze["sha256"]
    assert len(cases) >= 100 and len({c["id"] for c in cases}) == len(cases)
    assert freeze["control_prompts"] == DEFAULT_PROMPTS
    assert {c["language"] for c in cases} == {"de", "en"}
    for case in cases:
        for span in case["spans"]:
            assert case["text"][span["start"] : span["end"]] == span["text"]


def test_existing_conversation_suffix_truncation_preserves_utf8_characters():
    import ast
    from pathlib import Path

    tokenizer = offline_helpers().OfflineTokenizer("absent-cache")
    tokenizer._attempted = True
    path = Path(__file__).parents[2] / "generation/core_components/chunking.py"
    tree = ast.parse(path.read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "format_conversation_history"
    )
    scope = {"tokenizer": tokenizer, "count_tokens": lambda text: len(tokenizer.encode(text))}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), scope)
    source = "é" * 20001 + "🙂"
    output = scope["format_conversation_history"]([{"from": "human", "value": source}])
    assert output.startswith("...truncated...")
    payload = output.removeprefix("...truncated...")
    assert source.endswith(payload) and payload.endswith("🙂")
    assert tokenizer.decode(tokenizer.encode(payload)) == payload
