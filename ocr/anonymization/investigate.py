"""Explicit opt-in experiment on repository SYNTHETIC fixtures only.

Unlike aggregate evaluation, this artifact intentionally contains synthetic gold
and prediction text for diagnosis. Never use this command with private documents.
"""

import json
import resource
import time
from pathlib import Path

from .detectors import detect
from .evaluate import score_cases
from .model import MODEL_ID, MODEL_REVISION
from .normalization import dehyphenate
from .recognizer import DEFAULT_PROMPTS, GLiNERRecognizer
from .replacement import resolve_overlaps, validate_entities

REVIEWED_PROMPTS = DEFAULT_PROMPTS | {"CITY": "city", "ORGANIZATION": "organization"}

EXPERIMENTS = (
    ("reviewed_baseline", {}, 0.5),
    ("lower_threshold", {}, 0.35),
    ("geographic_prompt", {"CITY": "location"}, 0.5),
    (
        "geographic_and_company_prompt",
        {"CITY": "location", "ORGANIZATION": "company or organization"},
        0.5,
    ),
)


class Recorded:
    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.predictions = {}

    def recognize(self, text):
        found = self.recognizer.recognize(text)
        self.predictions[text] = found
        return found


def run(cache, output):
    start = time.perf_counter()
    model = GLiNERRecognizer(cache)
    loading_ms = (time.perf_counter() - start) * 1000
    root = Path(__file__).parent / "fixtures"
    suites = {
        n: json.loads((root / f"{n}.json").read_text()) for n in ("qualification", "validation")
    }
    report = dict(
        model=MODEL_ID,
        revision=MODEL_REVISION,
        loading_ms=loading_ms,
        experiments={},
        baseline_diagnosis=[],
    )
    model.recognize("The cat sat on the mat.")
    for name, changes, threshold in EXPERIMENTS:
        model.prompts = REVIEWED_PROMPTS | changes
        model.threshold = threshold
        recorder = Recorded(model)
        scores = {n: score_cases(recorder, cases) for n, cases in suites.items()}
        report["experiments"][name] = dict(
            prompts=model.prompts.copy(), threshold=threshold, suites=scores
        )
        if name != "reviewed_baseline":
            continue
        for suite, cases in suites.items():
            for case in cases:
                raw = recorder.predictions[case["text"]]
                combined = resolve_overlaps(
                    validate_entities(case["text"], raw + detect(case["text"]))
                )
                gold = {(s["start"], s["end"], s["label"]) for s in case["spans"]}
                predicted = {(s.start, s.end, s.label) for s in combined}
                errors = {
                    label: dict(
                        false_positives=[
                            s.model_dump()
                            for s in combined
                            if s.label == label and (s.start, s.end, s.label) not in gold
                        ],
                        false_negatives=[
                            s
                            for s in case["spans"]
                            if s["label"] == label
                            and (s["start"], s["end"], label) not in predicted
                        ],
                    )
                    for label in ("CITY", "ORGANIZATION")
                }
                if not any(v["false_positives"] or v["false_negatives"] for v in errors.values()):
                    continue
                normalized, _ = dehyphenate(case["text"])
                item = dict(
                    suite=suite,
                    case_id=case["id"],
                    text=case["text"],
                    normalized=normalized,
                    gold=case["spans"],
                    predictions=[s.model_dump() for s in combined],
                    errors=errors,
                )
                uncovered_city = [
                    s
                    for s in case["spans"]
                    if s["label"] == "CITY"
                    and any(
                        case["text"][i].isalnum()
                        and not any(p.start <= i < p.end for p in combined)
                        for i in range(s["start"], s["end"])
                    )
                ]
                if uncovered_city:
                    tokenizer = model.model.data_processor.transformer_tokenizer
                    ids = tokenizer(normalized)["input_ids"]
                    item["uncovered_city"] = uncovered_city
                    item["tokens"] = tokenizer.convert_ids_to_tokens(ids)
                    item["low_threshold_predictions"] = model.model.predict_entities(
                        normalized, list(model.prompts.values()), threshold=0.1, flat_ner=True
                    )
                report["baseline_diagnosis"].append(item)
    report["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare fixed experiments on synthetic repository corpora"
    )
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        run(args.cache, args.output)
    except Exception:
        raise SystemExit("synthetic_recognition_investigation_failed") from None
    print("synthetic_recognition_investigation_completed")
