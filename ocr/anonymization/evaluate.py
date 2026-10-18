"""Opt-in synthetic evaluation; records aggregate metrics, never document payloads."""

import importlib.metadata
import json
import resource
import statistics
import time
from collections import Counter
from pathlib import Path

from ocr.schemas import SafeError

from .detectors import detect
from .model import MODEL_ID, MODEL_REVISION
from .replacement import replace, resolve_overlaps, validate_entities
from .schemas import LABELS


def metrics(counts):
    output = {}
    for label in LABELS:
        tp, fp, fn = (counts[(label, name)] for name in ("tp", "fp", "fn"))
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        output[label] = dict(
            tp=tp, fp=fp, fn=fn, precision=p, recall=r, f1=2 * p * r / (p + r) if p + r else 0.0
        )
    return output


def score_cases(recognizer, cases):
    ner_counts, combined_counts, leaks, failures = Counter(), Counter(), Counter(), Counter()
    literal_leaks = Counter()
    partial_canaries, uncovered_canaries, coverage = Counter(), Counter(), Counter()
    durations, exact, blocked = [], 0, 0
    groups = {}
    for case in cases:
        text = case["text"]
        gold = {(s["start"], s["end"], s["label"]) for s in case["spans"]}
        for s in case["spans"]:
            if text[s["start"] : s["end"]] != s["text"]:
                raise SafeError("invalid_evaluation_fixture")
        start = time.perf_counter()
        actual, predicted, ner = None, set(), set()
        try:
            raw = validate_entities(text, recognizer.recognize(text))
            ner = {(s.start, s.end, s.label) for s in raw}
            combined = resolve_overlaps(validate_entities(text, raw + detect(text)))
            predicted = {(s.start, s.end, s.label) for s in combined}
            actual, _ = replace(text, combined)
        except SafeError as e:
            failures[e.code] += 1
            blocked += 1
        durations.append((time.perf_counter() - start) * 1000)
        for predicted_set, counts in ((ner, ner_counts), (predicted, combined_counts)):
            for _, _, label in predicted_set & gold:
                counts[(label, "tp")] += 1
            for _, _, label in predicted_set - gold:
                counts[(label, "fp")] += 1
            for _, _, label in gold - predicted_set:
                counts[(label, "fn")] += 1
        exact += actual == case["expected"]
        for s in case["spans"]:
            if actual is None or s["text"].casefold() in actual.casefold():
                literal_leaks[s["label"]] += 1
            sensitive_positions = [i for i in range(s["start"], s["end"]) if text[i].isalnum()]
            covered = sum(
                actual is not None and any(left <= i < right for left, right, _ in predicted)
                for i in sensitive_positions
            )
            coverage[(s["label"], "total")] += len(sensitive_positions)
            coverage[(s["label"], "covered")] += covered
            if covered < len(sensitive_positions):
                leaks[s["label"]] += 1
                if covered:
                    partial_canaries[s["label"]] += 1
                else:
                    uncovered_canaries[s["label"]] += 1
        group = case.get("language", "unspecified")
        stats = groups.setdefault(group, dict(documents=0, exact_matches=0, false_negatives=0))
        stats["documents"] += 1
        stats["exact_matches"] += actual == case["expected"]
        stats["false_negatives"] += len(gold - predicted)
        if case.get("ocr_noise"):
            stats = groups.setdefault(
                "ocr_noise", dict(documents=0, exact_matches=0, false_negatives=0)
            )
            stats["documents"] += 1
            stats["exact_matches"] += actual == case["expected"]
            stats["false_negatives"] += len(gold - predicted)
    return dict(
        documents=len(cases),
        ner_entity_metrics=metrics(ner_counts),
        combined_entity_metrics=metrics(combined_counts),
        unredacted_canaries={k: leaks[k] for k in LABELS},
        literal_unredacted_canaries={k: literal_leaks[k] for k in LABELS},
        partial_canaries={k: partial_canaries[k] for k in LABELS},
        fully_uncovered_canaries={k: uncovered_canaries[k] for k in LABELS},
        alphanumeric_canary_coverage={
            k: dict(covered=coverage[(k, "covered")], total=coverage[(k, "total")]) for k in LABELS
        },
        document_exact_match_rate=exact / len(cases),
        groups=groups,
        blocked_documents=blocked,
        failure_codes=dict(failures),
        warm_latency_ms=dict(median=statistics.median(durations), max=max(durations)),
        automated_release_eligible=False,
        qualification_passed=not any(leaks.values()) and blocked == 0,
    )


def run(cache, output):
    from .recognizer import GLiNERRecognizer

    start = time.perf_counter()
    recognizer = GLiNERRecognizer(cache)
    load = (time.perf_counter() - start) * 1000
    # Explicit warmup is synthetic public text, independently reported.
    recognizer.recognize("The cat sat on the mat.")
    report = dict(
        model=MODEL_ID,
        revision=MODEL_REVISION,
        threshold=recognizer.threshold,
        prompts=recognizer.prompts,
        runtime_versions={
            k: importlib.metadata.version(k)
            for k in ("gliner", "torch", "transformers", "numpy", "pydantic")
        },
        device="cpu",
        threads=2,
        loading_ms=load,
        suites={},
    )
    root = Path(__file__).parent / "fixtures"
    for name in ("qualification", "validation"):
        report["suites"][name] = score_cases(
            recognizer, json.loads((root / f"{name}.json").read_text())
        )
    report["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    report["automated_release_eligible"] = False
    report["recognition_qualification_passed"] = all(
        s["qualification_passed"] for s in report["suites"].values()
    )
    Path(output).write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pinned local synthetic model evaluation")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = run(args.cache, args.output)
    except Exception:
        raise SystemExit("anonymization_evaluation_failed") from None
    print(
        json.dumps(
            {
                "recognition_qualification_passed": report["recognition_qualification_passed"],
                "automated_release_eligible": False,
            }
        )
    )
