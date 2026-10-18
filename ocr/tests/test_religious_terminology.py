"""Independent unit fixtures for lexical protection, never attribution to people."""

import pytest
from ocr.anonymization.replacement import replace, validate_entities
from ocr.anonymization.terminology import ReligiousTerminology
from ocr.schemas import SafeError


@pytest.mark.parametrize(
    "text,terms,expected",
    [
        ("Status: religionslos.", ("religionslos",), ["religionslos"]),
        ("Angabe: OHNE KONFESSION", ("ohne Konfession",), ["OHNE KONFESSION"]),
        (
            "No religious affiliation was stated.",
            ("no religious affiliation",),
            ["No religious affiliation"],
        ),
        ("A lecture on Taoism, not a personal assertion.", ("Taoism",), ["Taoism"]),
        ("She denied being a Jain.", ("Jain",), ["Jain"]),
        ('He quoted "Zoroastrianism".', ("Zoroastrianism",), ["Zoroastrianism"]),
        ("Préface: Straße / STRASSE.", ("Straße",), ["Straße", "STRASSE"]),
        ("💡 A Bahá'í reference.", ("Bahá'í",), ["Bahá'í"]),
        ("An agnos-\ntisch entry.", ("agnostisch",), ["agnos-\ntisch"]),
        ("Hinduism Hinduism", ("Hinduism",), ["Hinduism", "Hinduism"]),
        ("Islamabad Islamismus preIslam Islam_suffix Islam\u0301", ("Islam",), []),
        ("orthodox sorting; catholic taste; evangelical marketing", ("Islam",), []),
        ("Buddh1sm is an OCR error", ("Buddhism",), []),
    ],
)
def test_literal_offsets_and_no_attribution(text, terms, expected):
    found = ReligiousTerminology(terms).detect(text)
    assert [p.text for p in sorted(found, key=lambda p: p.start)] == expected
    validate_entities(text, found)
    assert all(p.score is None and p.source == "deterministic" for p in found)
    assert all(p.label == "RELIGION" for p in found)
    assert all(
        set(p.model_dump())
        == {"start", "end", "label", "text", "score", "source", "model_revision", "normalization"}
        for p in found
    )
    sanitized, _ = replace(text, found)
    assert sanitized.count("[RELIGION]") == len(expected)


@pytest.mark.parametrize(
    "terms",
    [(), [], [""], [" x"], ["a.*"], ["a\nb"], ["a" * 81], [None], ["a"] * 257, "Islam", {"Islam"}],
)
def test_reject_unbounded_or_nonliteral_configuration(terms):
    with pytest.raises(SafeError, match="invalid_terminology_configuration"):
        ReligiousTerminology(terms)


def test_casefold_never_matches_partial_expansion():
    assert not ReligiousTerminology(("s",)).detect("ß")


def test_length_limit_and_default_is_not_affiliation_inference():
    detector = ReligiousTerminology()
    with pytest.raises(SafeError, match="anonymization_text_limit"):
        detector.detect("x" * 2_000_001)
    found = detector.detect("A report about Sikhism says nothing about the attendee.")
    assert [p.text for p in found] == ["Sikhism"]
    assert not detector.detect("An orthodox algorithm handles an unaffiliated candidate.")


def test_longest_containing_phrase_preserves_offsets_and_has_no_fake_score():
    found = ReligiousTerminology(("no religion", "religion")).detect("Reported: no religion.")
    out, tags = replace("Reported: no religion.", found)
    assert out == "Reported: [RELIGION]." and len(tags) == 1
    assert tags[0].score is None and tags[0].model_revision == "religious-terminology-v1"


def test_engine_keeps_d2_and_review_flags_with_only_terminology_evidence():
    from ocr.anonymization.engine import AnonymizationEngine
    from ocr.anonymization.model import MODEL_REVISION
    from ocr.anonymization.schemas import LABELS
    from ocr.tests.test_anonymization_security import evidence

    class LocalEmpty:
        local, revision, categories = True, MODEL_REVISION, LABELS

        def recognize(self, text):
            return []

    candidate = AnonymizationEngine(LocalEmpty()).handoff(evidence("D2", ("The topic is Taoism.",)))
    assert candidate.pages[0].anonymized_text == "The topic is [RELIGION]."
    assert candidate.sensitivity == candidate.pages[0].original_sensitivity == "D2"
    assert not candidate.anonymous and not candidate.automated_release_eligible
    assert candidate.release_review_required and not candidate.release_approved
    assert "empty_prediction" in candidate.review_reasons


def test_terminology_does_not_hide_model_failure_or_overlap():
    from ocr.anonymization.engine import AnonymizationEngine
    from ocr.anonymization.model import MODEL_REVISION
    from ocr.anonymization.schemas import LABELS, Entity

    class Broken:
        local, revision, categories = True, MODEL_REVISION, LABELS

        def recognize(self, text):
            raise RuntimeError("private payload in model exception")

    with pytest.raises(SafeError, match="^anonymization_failed$"):
        AnonymizationEngine(Broken()).transform("Taoism")

    class Conflict(Broken):
        def recognize(self, text):
            return [
                Entity(
                    start=0,
                    end=6,
                    text=text,
                    label="PERSON",
                    score=0.8,
                    source="multilingual_ner",
                    model_revision=MODEL_REVISION,
                )
            ]

    with pytest.raises(SafeError, match="ambiguous_entity_overlap"):
        AnonymizationEngine(Conflict()).transform("Taoism")


def test_custom_operator_terminology_uses_same_provenance_validation():
    from ocr.anonymization.detectors import detect

    found = detect("A Rastafari topic.", terminology=ReligiousTerminology(("Rastafari",)))
    assert len(found) == 1 and found[0].text == "Rastafari"
    assert not detect("Rastafari")


def test_new_frozen_holdout_is_independent_well_formed_and_complete():
    import json
    from pathlib import Path

    from ocr.anonymization.qualify03d import verify_freeze

    root, freeze, cases = verify_freeze()
    assert len(cases) >= 150 and freeze["evaluation_run_before_freeze"] is False
    assert sum(c["language"] == "de" for c in cases) == 80
    assert sum(c["language"] == "en" for c in cases) == 80
    assert len({c["scenario"] for c in cases}) == 10
    oldroot = Path(__file__).parents[1] / "anonymization/fixtures"
    oldtexts = {
        c["text"]
        for p in (
            oldroot / "qualification.json",
            oldroot / "validation.json",
            oldroot / "holdout03c/cases.json",
        )
        for c in json.loads(p.read_bytes())
    }
    assert not oldtexts.intersection(c["text"] for c in cases)
    for case in cases:
        reconstructed = case["text"]
        for span in reversed(case["spans"]):
            assert case["text"][span["start"] : span["end"]] == span["text"]
            reconstructed = (
                reconstructed[: span["start"]]
                + "["
                + span["label"]
                + "]"
                + reconstructed[span["end"] :]
            )
        assert reconstructed == case["expected"]
    assert root.name == "holdout03d"


def test_existing_first_result_refuses_before_initializing_model(tmp_path, monkeypatch):
    from ocr.anonymization import qualify03d

    target = tmp_path / "immutable.json"
    target.write_bytes(b"first result")
    monkeypatch.setattr(
        qualify03d, "GLiNERRecognizer", lambda *a, **k: pytest.fail("must not infer twice")
    )
    with pytest.raises(FileExistsError, match="first_result_already_exists"):
        qualify03d.run("unused-cache", target)
    assert target.read_bytes() == b"first result"
