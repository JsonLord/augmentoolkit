"""First-result, frozen synthetic holdout; refuses prompt/weight changes."""

import hashlib
import importlib.metadata
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

from .atomic_output import write_candidate
from .evaluate import score_cases
from .model import MODEL_REVISION
from .recognizer import GLiNERRecognizer


def run(cache, output):
    root = Path(__file__).parent / "fixtures/holdout03c"
    frozen = json.loads((root / "freeze.json").read_text())
    data = (root / "cases.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != frozen["sha256"]:
        raise ValueError("holdout_integrity_failed")
    denied = []

    def no_network(event, args):
        if event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
            denied.append(event)
            raise PermissionError("qualification_network_disabled")

    sys.addaudithook(no_network)
    started = time.perf_counter()
    recognizer = GLiNERRecognizer(cache)
    loading_ms = (time.perf_counter() - started) * 1000
    if (
        recognizer.prompts != frozen["control_prompts"]
        or recognizer.threshold != frozen["threshold"]
        or MODEL_REVISION != frozen["model_revision"]
    ):
        raise ValueError("holdout_control_changed")
    recognizer.recognize("A plain synthetic warmup sentence.")
    report = dict(
        schema_version="holdout03c-first-control/v1",
        corpus_sha256=frozen["sha256"],
        code_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        freeze_commit=subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", str(root / "cases.json")], text=True
        ).strip(),
        model_revision=MODEL_REVISION,
        prompts=recognizer.prompts,
        threshold=recognizer.threshold,
        loading_ms=loading_ms,
        runtime_versions={
            k: importlib.metadata.version(k)
            for k in ("gliner", "torch", "transformers", "numpy", "pydantic")
        },
        evaluation=score_cases(recognizer, json.loads(data)),
        peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        denied_network_calls=denied,
        automated_release_eligible=False,
    )
    write_candidate(output, (json.dumps(report, indent=2) + "\n").encode())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Evaluate frozen holdout once with existing control"
    )
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        run(args.cache, args.output)
    except Exception:
        raise SystemExit("holdout_evaluation_failed") from None
    print("holdout_evaluation_recorded_check_qualification_result")
