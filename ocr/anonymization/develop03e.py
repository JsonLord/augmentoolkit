"""One pinned inference pass, controlled replay of existing development evidence.

Synthetic-only, fixed fixture paths. No fresh holdout or release qualification.
Model timing is recorded independently from replay/deterministic evaluation time.
"""

import hashlib
import importlib.metadata
import json
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

from .atomic_output import write_candidate
from .detectors import detect_contacts
from .outcomes import score_outcomes
from .recognizer import GLiNERRecognizer
from .status import StatusPolicy
from .terminology import ReligiousTerminology


def run(cache, output):
    if Path(output).exists():
        raise FileExistsError("development_result_exists")
    denied = []

    def offline(event, args):
        if event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
            denied.append(event)
            raise PermissionError("development_network_disabled")

    sys.addaudithook(offline)
    root = Path(__file__).parent / "fixtures"
    paths = {
        "qualification": root / "qualification.json",
        "validation": root / "validation.json",
        "development03c": root / "holdout03c/cases.json",
        "development03d": root / "holdout03d/cases.json",
    }
    suites = {n: json.loads(p.read_bytes()) for n, p in paths.items()}
    start = time.perf_counter()
    model = GLiNERRecognizer(cache)
    loading_ms = (time.perf_counter() - start) * 1000
    model.recognize("An independent synthetic warmup sentence.")
    predictions, inference = {}, {}
    for cases in suites.values():
        for case in cases:
            text = case["text"]
            if text in predictions:
                continue
            start = time.perf_counter()
            predictions[text] = model.recognize(text)
            inference[text] = (time.perf_counter() - start) * 1000

    class Replay:
        def recognize(self, text):
            return predictions[text]

    terms = ReligiousTerminology()
    report = dict(
        schema_version="gate03e-development-comparison/v1",
        source_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        corpus_sha256={n: hashlib.sha256(p.read_bytes()).hexdigest() for n, p in paths.items()},
        model_revision=model.revision,
        prompts=model.prompts,
        threshold=model.threshold,
        loading_ms=loading_ms,
        actual_inference_calls=len(predictions),
        experiments={},
        runtime_versions={
            k: importlib.metadata.version(k)
            for k in ("gliner", "torch", "transformers", "numpy", "pydantic")
        },
        actual_inference_latency_ms={
            n: dict(
                median=statistics.median(inference[c["text"]] for c in cases),
                max=max(inference[c["text"]] for c in cases),
            )
            for n, cases in suites.items()
        },
        automated_release_eligible=False,
        independent_qualification=False,
    )
    for name, status in (
        ("gate03d_literal_control", None),
        ("contextual_status", StatusPolicy(ocr_enabled=False)),
        ("status_scoped_ocr", StatusPolicy()),
    ):

        def detector(text, status=status):
            return (
                detect_contacts(text)
                + terms.detect(text)
                + ([] if status is None else status.detect(text))
            )

        results = {
            n: score_outcomes(Replay(), cases, detector=detector) for n, cases in suites.items()
        }
        for result in results.values():
            result["accuracy"]["deterministic_replay_latency_ms"] = result["accuracy"].pop(
                "warm_latency_ms"
            )
        report["experiments"][name] = results
    baseline = report["experiments"]["gate03d_literal_control"]
    report["selection_criteria"] = {}
    for name, suites_result in report["experiments"].items():
        if name == "gate03d_literal_control":
            continue
        okay, improvements = True, 0
        for suite, result in suites_result.items():
            before = baseline[suite]
            okay &= result["candidate_return_rate"] >= before["candidate_return_rate"]
            okay &= (
                result["accuracy"]["document_exact_match_rate"]
                >= before["accuracy"]["document_exact_match_rate"]
            )
            for metric in ("candidate_partial_canaries", "candidate_fully_unmasked_canaries"):
                okay &= all(result[metric][k] <= before[metric][k] for k in result[metric])
                improvements += sum(before[metric].values()) - sum(result[metric].values())
            okay &= all(
                result["accuracy"]["combined_entity_metrics"][k]["fp"] <= v["fp"]
                for k, v in before["accuracy"]["combined_entity_metrics"].items()
            )
        report["selection_criteria"][name] = dict(
            passed=bool(okay and improvements > 0), net_canary_improvements=improvements
        )
    report["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    report["denied_network_calls"] = denied
    write_candidate(output, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pinned development comparison, not a holdout")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(args.cache, args.output)
    print(json.dumps(result["selection_criteria"]))
