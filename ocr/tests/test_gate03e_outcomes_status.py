"""Synthetic category/coverage separation and bounded status-word evidence."""

import pytest
from ocr.anonymization.outcomes import score_outcomes
from ocr.anonymization.replacement import replace
from ocr.anonymization.schemas import Entity
from ocr.anonymization.status import StatusPolicy
from ocr.schemas import SafeError


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Angabe: keine Konfession.", ["keine Konfession"]),
        ("Er bestritt 'keiner Religion' anzugehören.", ["keiner Religion"]),
        ("A religiously uncommitted position is quoted.", ["religiously uncommitted"]),
        ("The essay concerns religiously unaffiliated citizens.", ["religiously unaffiliated"]),
        ("Ohne Religion, nicht ohne Humor.", ["Ohne Religion"]),
        ("Status: NONRELIGI0US", ["NONRELIGI0US"]),
        ("Scan: konfessionsfre1", ["konfessionsfre1"]),
        ("Scan: konfessi0nsfrei", ["konfessi0nsfrei"]),
        ("💡 nonreli-\ngi0us", ["nonreli-\ngi0us"]),
    ],
)
def test_general_status_grammar_and_exact_offsets(text, expected):
    predictions = StatusPolicy().detect(text)
    assert [p.text for p in predictions] == expected
    assert all(p.score is None and p.source == "deterministic" for p in predictions)
    output, _ = replace(text, predictions)
    assert output.count("[RELIGION]") == len(expected)
    assert all(text[p.start : p.end] == p.text for p in predictions)


@pytest.mark.parametrize(
    "text",
    [
        "politically unaffiliated",
        "Religiously sophisticated art",
        "ohne Religionsbuch",
        "keine Religionszugehörigkeit behauptet",
        "nonreligiousness",
        "nonreligi00s",
        "prenom_nonreligi0us",
        "nonreligi0us_suffix",
        "übernonreligi0us",
        "none religious",
        "a 12-character number",
        "nonreligi0us\u0301",
        "Archivs",
        "Hafenbogen",
        "konfessXonsfrei",
        "konfess10nsfre1",
    ],
)
def test_no_broad_fuzzy_matching_or_attribution(text):
    assert not StatusPolicy().detect(text)


def test_disable_ocr_and_bounded_operator_policy():
    assert not StatusPolicy(ocr_enabled=False).detect("NONRELIGI0US")
    for kwargs in (
        {"ocr_enabled": 1},
        {"ocr_terms": ()},
        {"ocr_terms": ("short",)},
        {"ocr_terms": ("a.*",)},
        {"ocr_terms": ("a" * 41,)},
    ):
        with pytest.raises(SafeError, match="invalid_status_configuration"):
            StatusPolicy(**kwargs)


def test_span_conflicts_are_abstentions_not_returned_leaks():
    text = "Synthetic"
    gold = dict(start=0, end=9, text=text, label="RELIGION")
    case = dict(id="unit", text=text, spans=[gold], expected="[RELIGION]")

    class Recognizer:
        def recognize(self, text):
            return [
                Entity(
                    **(gold | {"label": "PERSON"}), source="deterministic", model_revision="unit"
                )
            ]

    report = score_outcomes(
        Recognizer(),
        [case],
        detector=lambda t: [Entity(**gold, source="deterministic", model_revision="unit")],
    )
    assert report["candidate_outcomes"]["blocked_conflicting_spans"] == 1
    assert report["safe_abstention_rate"] == 1 and report["candidate_return_rate"] == 0
    assert sum(report["candidate_fully_unmasked_canaries"].values()) == 0
    assert sum(report["candidate_partial_canaries"].values()) == 0
    assert report["no_measured_leakage_rate_among_returned"] is None
    assert report["accuracy"]["uncovered_source_canaries_including_incomplete"]["RELIGION"] == 1
    assert report["accuracy"]["document_exact_match_rate"] == 0
    assert not report["recognition_qualified"]


@pytest.mark.parametrize(
    "code,outcome",
    [
        ("anonymization_inference_failed", "blocked_model_runtime"),
        ("incomplete_ocr_document", "other_incomplete"),
        ("anonymization_text_limit", "other_incomplete"),
    ],
)
def test_other_failures_never_count_as_success(code, outcome):
    class Failed:
        def recognize(self, text):
            raise SafeError(code)

    report = score_outcomes(
        Failed(),
        [dict(id="negative", text="plain", spans=[], expected="plain")],
        detector=lambda t: [],
    )
    assert report["candidate_outcomes"][outcome] == 1
    assert report["candidate_outcomes"]["complete_no_measured_leakage"] == 0
    assert report["candidate_return_rate"] == 0


def test_partial_full_and_taxonomy_mismatch_are_distinct():
    cases = [
        dict(
            id=str(i),
            text="abc def",
            spans=[dict(start=0, end=7, text="abc def", label="PERSON")],
            expected="[PERSON]",
        )
        for i in range(3)
    ]

    class Model:
        def __init__(self):
            self.n = 0

        def recognize(self, text):
            self.n += 1
            if self.n == 1:
                return []
            return [
                Entity(
                    start=0,
                    end=3 if self.n == 2 else 7,
                    text=text[:3] if self.n == 2 else text,
                    label="CITY",
                    source="deterministic",
                    model_revision="unit",
                )
            ]

    report = score_outcomes(Model(), cases, detector=lambda t: [])
    assert [p["outcome"] for p in report["per_case"]] == [
        "candidate_fully_unmasked",
        "candidate_partial_mask",
        "complete_no_measured_leakage",
    ]
    assert report["accuracy"]["document_exact_match_rate"] == 0
    assert report["returned_candidate_entity_metrics"]["PERSON"]["fn"] == 3
    assert (
        report["candidate_partial_canaries"]["PERSON"]
        == report["candidate_fully_unmasked_canaries"]["PERSON"]
        == 1
    )


def test_unexpected_runtime_failure_is_sanitized_and_abstains():
    class Broken:
        def recognize(self, text):
            raise RuntimeError("SYNTHETIC_PRIVATE_RUNTIME_PAYLOAD")

    result = score_outcomes(Broken(), [dict(id="unit", text="plain", spans=[], expected="plain")])
    assert result["candidate_outcomes"]["blocked_model_runtime"] == 1
    assert result["per_case"][0]["error_code"] == "anonymization_inference_failed"
    assert "SYNTHETIC_PRIVATE_RUNTIME_PAYLOAD" not in str(result)


def test_historical_first_runner_refuses_updated_implementation():
    from ocr.anonymization.qualify03d import verify_freeze

    with pytest.raises(ValueError, match="holdout_integrity_or_configuration_changed"):
        verify_freeze()
