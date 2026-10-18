"""Development accuracy and actual candidate coverage, with explicit abstentions.

The historical evaluator and its saved first reports are retained unchanged.
This reporting interface separates blocked evidence from emitted candidate text.
"""

from collections import Counter

from ocr.schemas import SafeError

from .detectors import detect
from .evaluate import metrics, score_cases
from .schemas import LABELS

OUTCOMES = (
    "complete_no_measured_leakage",
    "candidate_partial_mask",
    "candidate_fully_unmasked",
    "blocked_conflicting_spans",
    "blocked_model_runtime",
    "other_incomplete",
)
RUNTIME_FAILURES = frozenset(
    ("anonymization_model_unavailable", "anonymization_inference_failed", "anonymization_failed")
)


def score_outcomes(recognizer, cases, *, detector=detect):
    class SafeRecognizer:
        def recognize(self, text):
            try:
                return recognizer.recognize(text)
            except SafeError:
                raise
            except Exception:
                raise SafeError("anonymization_inference_failed") from None

    def safe_detector(text):
        try:
            return detector(text)
        except SafeError:
            raise
        except Exception:
            raise SafeError("anonymization_failed") from None

    details = []
    accuracy = score_cases(SafeRecognizer(), cases, detector=safe_detector, case_reports=details)
    # Historical aggregate coverage counts incomplete documents as uncovered.
    # Name that conservative scope explicitly; it is not emitted-candidate leakage.
    for old, new in (
        ("unredacted_canaries", "uncovered_source_canaries_including_incomplete"),
        ("literal_unredacted_canaries", "literal_presence_or_incomplete"),
        ("partial_canaries", "partially_covered_source_canaries_including_incomplete"),
        ("fully_uncovered_canaries", "fully_uncovered_source_canaries_including_incomplete"),
        ("alphanumeric_canary_coverage", "all_document_source_coverage"),
        ("qualification_passed", "conservative_accuracy_gate_passed"),
    ):
        accuracy[new] = accuracy.pop(old)
    accuracy["scope"] = (
        "all source spans; incomplete processing counts as missing, not leaked output"
    )
    counts, partial, full, candidate_spans = Counter(), Counter(), Counter(), Counter()
    for case, detail in zip(cases, details, strict=True):
        error = detail["error_code"]
        if error:
            outcome = (
                "blocked_conflicting_spans"
                if error == "ambiguous_entity_overlap"
                else "blocked_model_runtime"
                if error in RUNTIME_FAILURES
                else "other_incomplete"
            )
            detail["candidate_returned"] = False
            detail["candidate_canaries"] = []
        else:
            flags = []
            for span in detail["canaries"]:
                missing = span["uncovered_positions"]
                total = sum(c.isalnum() for c in span["text"])
                if missing:
                    kind = "fully_unmasked" if len(missing) == total else "partial_mask"
                    (full if kind == "fully_unmasked" else partial)[span["label"]] += 1
                    flags.append(
                        dict(label=span["label"], start=span["start"], end=span["end"], kind=kind)
                    )
            detail["candidate_returned"] = True
            detail["candidate_canaries"] = flags
            outcome = (
                "candidate_fully_unmasked"
                if any(f["kind"] == "fully_unmasked" for f in flags)
                else "candidate_partial_mask"
                if flags
                else "complete_no_measured_leakage"
            )
            gold = {(s["start"], s["end"], s["label"]) for s in case["spans"]}
            predicted = {(s["start"], s["end"], s["label"]) for s in detail["combined_predictions"]}
            for labelset, kind in (
                (gold & predicted, "tp"),
                (predicted - gold, "fp"),
                (gold - predicted, "fn"),
            ):
                for _, _, label in labelset:
                    candidate_spans[(label, kind)] += 1
        detail["outcome"] = outcome
        counts[outcome] += 1
    returned = sum(counts[k] for k in OUTCOMES[:3])
    safe = counts["blocked_conflicting_spans"] + counts["blocked_model_runtime"]
    return dict(
        schema_version="development-candidate-outcomes/v1",
        accuracy=accuracy,
        candidate_outcomes={k: counts[k] for k in OUTCOMES},
        candidate_return_rate=returned / len(cases),
        safe_abstention_rate=safe / len(cases),
        incomplete_rate=(len(cases) - returned) / len(cases),
        no_measured_leakage_rate_among_returned=counts[OUTCOMES[0]] / returned
        if returned
        else None,
        candidate_partial_canaries={k: partial[k] for k in LABELS},
        candidate_fully_unmasked_canaries={k: full[k] for k in LABELS},
        returned_candidate_entity_metrics=metrics(candidate_spans),
        per_case=details,
        automated_release_eligible=False,
        recognition_qualified=False,  # development evidence is not independent qualification
    )
