"""Actual pinned recognition is separate from deterministic boundary tests."""

import json
import os
from pathlib import Path

import pytest
from ocr.anonymization import AnonymizationEngine
from ocr.anonymization.detectors import detect_contacts
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


@pytest.mark.skipif(
    os.getenv("RUN_GATE03C_HOLDOUT") != "1", reason="Opt-in historical Gate03c control"
)
def test_gate03c_frozen_holdout_canary_acceptance():
    import hashlib

    root = Path(__file__).parents[1] / "anonymization/fixtures/holdout03c"
    frozen = json.loads((root / "freeze.json").read_text())
    data = (root / "cases.json").read_bytes()
    assert hashlib.sha256(data).hexdigest() == frozen["sha256"]
    model = GLiNERRecognizer(os.environ["ANONYMIZATION_MODEL_CACHE"])
    assert model.prompts == frozen["control_prompts"] and model.threshold == frozen["threshold"]
    report = score_cases(model, json.loads(data), detector=detect_contacts)
    assert report["qualification_passed"], (
        "Historical Gate03c control FAILED; original first-result evidence remains unchanged"
    )


@pytest.mark.skipif(
    os.getenv("RUN_GATE03D_MODEL") != "1", reason="Opt-in selected hybrid development regression"
)
def test_gate03d_selected_hybrid_development_regression():
    model = GLiNERRecognizer(os.environ["ANONYMIZATION_MODEL_CACHE"])
    root = Path(__file__).parents[1] / "anonymization/fixtures"
    for relative in ("qualification.json", "validation.json", "holdout03c/cases.json"):
        assert score_cases(model, json.loads((root / relative).read_bytes()))[
            "qualification_passed"
        ]


@pytest.mark.skipif(
    os.getenv("RUN_GATE03D_HOLDOUT") != "1", reason="Opt-in new independent first-result holdout"
)
def test_gate03d_recorded_first_holdout_acceptance():
    # The standalone first-result command performs inference once. This test
    # checks that actual recorded gate without rerunning or overwriting it.
    from ocr.anonymization.qualify03d import verify_freeze

    _, freeze, cases = verify_freeze()
    report = json.loads(Path(os.environ["GATE03D_HOLDOUT_OUTPUT"]).read_bytes())
    assert report["corpus_sha256"] == freeze["sha256"]
    assert report["model_revision"] == freeze["model_revision"]
    assert report["implementation_sha256"] == freeze["implementation_sha256"]
    assert {p["case_id"] for p in report["per_case"]} == {c["id"] for c in cases}
    assert report["evaluation"]["qualification_passed"], (
        "Gate03d untouched holdout FAILED; first result preserved, release blocked"
    )
