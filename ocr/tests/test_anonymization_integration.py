"""Actual pinned recognition is separate from deterministic boundary tests."""

import json
import os
from pathlib import Path

import pytest
from ocr.anonymization import AnonymizationEngine
from ocr.anonymization.evaluate import score_cases
from ocr.anonymization.recognizer import GLiNERRecognizer
from ocr.handoff import anonymization_handoff
from ocr.tests.test_anonymization_security import evidence


@pytest.mark.skipif(
    os.getenv("RUN_ANONYMIZATION_MODEL") != "1", reason="Explicit opt-in pinned local model"
)
def test_real_pinned_recognition_and_ocr_handoff():
    cache = os.environ["ANONYMIZATION_MODEL_CACHE"]
    model = GLiNERRecognizer(cache)
    root = Path(__file__).parents[1] / "anonymization" / "fixtures"
    validation = json.loads((root / "validation.json").read_text())
    text = validation[0]["text"]
    result = anonymization_handoff(evidence("D2", (text, text)), AnonymizationEngine(model))
    assert len(result.pages) == 2 and result.sensitivity == "D2"
    assert "[PERSON]" in result.pages[0].anonymized_text
    assert not result.release_approved and result.release_review_required


@pytest.mark.skipif(
    os.getenv("RUN_ANONYMIZATION_MODEL") != "1", reason="Explicit opt-in pinned local model"
)
def test_real_synthetic_canary_release_qualification():
    model = GLiNERRecognizer(os.environ["ANONYMIZATION_MODEL_CACHE"])
    root = Path(__file__).parents[1] / "anonymization" / "fixtures"
    reports = [
        score_cases(model, json.loads((root / f"{name}.json").read_text()))
        for name in ("qualification", "validation")
    ]
    # Quality qualification is deliberately stricter than smoke recognition.
    # A nonzero canary count is a failing gate, not an xfail or an assumed pass.
    assert all(r["qualification_passed"] for r in reports), (
        "Pinned recognizer has leakage; automated release blocked"
    )
