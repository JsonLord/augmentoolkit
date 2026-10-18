"""Development-only fixed experiments, never a claim of independent holdout quality.

All input paths are fixed repository synthetic corpora. Per-case payload evidence
is intentionally synthetic. The three datasets are development data from Gate03d.
"""

import hashlib
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

from .atomic_output import write_candidate
from .detectors import detect_contacts as detect
from .evaluate import score_cases
from .investigate import Recorded
from .recognizer import DEFAULT_PROMPTS, GLiNERRecognizer
from .replacement import replace, resolve_overlaps, validate_entities
from .terminology import DEFAULT_TERMS, ReligiousTerminology


def run(cache, output):
    denied = []

    def offline(event, args):
        if event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
            denied.append(event)
            raise PermissionError("qualification_network_disabled")

    sys.addaudithook(offline)
    start = time.perf_counter()
    model = GLiNERRecognizer(cache)
    load = (time.perf_counter() - start) * 1000
    model.recognize("Synthetic warmup text.")
    root = Path(__file__).parent / "fixtures"
    paths = {
        "qualification": root / "qualification.json",
        "validation": root / "validation.json",
        "development03c": root / "holdout03c/cases.json",
    }
    suites = {n: json.loads(p.read_bytes()) for n, p in paths.items()}
    terminology = ReligiousTerminology()

    def hybrid(text):
        return detect(text) + terminology.detect(text)

    report = dict(
        schema_version="gate03d-development-comparison/v1",
        source_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        model_revision=model.revision,
        corpus_sha256={n: hashlib.sha256(p.read_bytes()).hexdigest() for n, p in paths.items()},
        terminology=list(DEFAULT_TERMS),
        loading_ms=load,
        experiments={},
        automated_release_eligible=False,
    )
    baseline = {}
    # Fixed before inference. No low-threshold sweep or fixture-specific patterns.
    for name, prompts, detector in (
        ("control", DEFAULT_PROMPTS, detect),
        (
            "semantic_status_prompt",
            DEFAULT_PROMPTS | {"RELIGION": "religion or religious non-affiliation"},
            detect,
        ),
        ("hybrid_terminology", DEFAULT_PROMPTS, hybrid),
    ):
        model.prompts = dict(prompts)
        recorder = Recorded(model)
        result = dict(prompts=model.prompts, threshold=model.threshold, suites={}, changes=[])
        for suite, cases in suites.items():
            result["suites"][suite] = score_cases(recorder, cases, detector=detector)
            for case in cases:
                text = case["text"]
                raw = recorder.predictions[text]
                combined = resolve_overlaps(validate_entities(text, raw + detector(text)))
                output_text, _ = replace(text, combined)
                predicted = {(p.start, p.end, p.label) for p in combined}
                gold = {(p["start"], p["end"], p["label"]) for p in case["spans"]}
                item = dict(
                    suite=suite,
                    case_id=case["id"],
                    false_positives=sorted(predicted - gold),
                    false_negatives=sorted(gold - predicted),
                    exact_match=output_text == case["expected"],
                    predictions=[p.model_dump() for p in combined],
                )
                key = (suite, case["id"])
                if name == "control":
                    baseline[key] = item
                    if item["false_positives"] or item["false_negatives"]:
                        result["changes"].append(item)
                elif item["predictions"] != baseline[key]["predictions"]:
                    result["changes"].append(dict(before=baseline[key], after=item))
        report["experiments"][name] = result
    report["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    report["denied_network_calls"] = denied
    write_candidate(output, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Compare fixed Gate03d development alternatives")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.cache, args.output)
    print("comparison_recorded_check_metrics_before_selection")
