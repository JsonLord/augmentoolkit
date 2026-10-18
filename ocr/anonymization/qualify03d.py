"""First-result evaluation of the frozen Gate03d synthetic holdout.

No prompt/threshold search, no remote access, no release authorization. Corpus,
configuration and implementation hashes are checked before model initialization.
An exclusive output is mandatory; the report is saved before acceptance is tested.
"""

import hashlib
import importlib.metadata
import json
import resource
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .atomic_output import write_candidate
from .detectors import detect
from .evaluate import score_cases
from .model import MODEL_REVISION
from .recognizer import DEFAULT_PROMPTS, GLiNERRecognizer
from .terminology import DEFAULT_TERMS


def verify_freeze():
    root = Path(__file__).parent / "fixtures/holdout03d"
    freeze = json.loads((root / "freeze.json").read_bytes())
    data = (root / "cases.json").read_bytes()
    repo = Path(__file__).parents[2]
    if (
        hashlib.sha256(data).hexdigest() != freeze["sha256"]
        or freeze["model_revision"] != MODEL_REVISION
        or freeze["prompts"] != DEFAULT_PROMPTS
        or freeze["threshold"] != 0.5
        or freeze["religion_terms"] != list(DEFAULT_TERMS)
        or any(
            hashlib.sha256((repo / name).read_bytes()).hexdigest() != digest
            for name, digest in freeze["implementation_sha256"].items()
        )
    ):
        raise ValueError("holdout_integrity_or_configuration_changed")
    cases = json.loads(data)
    if len(cases) != freeze["cases"] or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("holdout_integrity_failed")
    return root, freeze, cases


def run(cache, output):
    if Path(output).exists():
        raise FileExistsError("first_result_already_exists")
    root, freeze, cases = verify_freeze()
    denied = []

    def offline(event, args):
        if event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
            denied.append(event)
            raise PermissionError("qualification_network_disabled")

    sys.addaudithook(offline)
    started = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()
    model = GLiNERRecognizer(cache)
    load_ms = (time.perf_counter() - start) * 1000
    model.recognize("A synthetic warmup sentence.")
    details = []
    evaluation = score_cases(model, cases, case_reports=details)
    # Cost of the deterministic layer separately, with no extra model inference.
    rule_ms = []
    for case in cases:
        start = time.perf_counter()
        detect(case["text"])
        rule_ms.append((time.perf_counter() - start) * 1000)
    report = dict(
        schema_version="gate03d-independent-first-selected/v1",
        started_utc=started,
        finished_utc=datetime.now(timezone.utc).isoformat(),
        code_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        freeze_commit=subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", str(root / "cases.json")], text=True
        ).strip(),
        corpus_sha256=freeze["sha256"],
        implementation_sha256=freeze["implementation_sha256"],
        model_revision=MODEL_REVISION,
        prompts=model.prompts,
        threshold=model.threshold,
        religion_terms=list(DEFAULT_TERMS),
        runtime_versions={
            k: importlib.metadata.version(k)
            for k in ("gliner", "torch", "transformers", "numpy", "pydantic")
        },
        loading_ms=load_ms,
        evaluation=evaluation,
        per_case=details,
        deterministic_latency_ms=dict(median=statistics.median(rule_ms), max=max(rule_ms)),
        peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        denied_network_calls=denied,
        automated_release_eligible=False,
    )
    write_candidate(output, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate frozen Gate03d holdout once")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(args.cache, args.output)
    print(
        json.dumps(
            dict(
                qualification_passed=result["evaluation"]["qualification_passed"],
                automated_release_eligible=False,
            )
        )
    )
    raise SystemExit(0 if result["evaluation"]["qualification_passed"] else 1)
