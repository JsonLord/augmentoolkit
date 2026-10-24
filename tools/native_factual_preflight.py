"""Import-only native qualification. No generation, Hub writes or HF Job launch."""

import argparse
import contextlib
import importlib
import inspect
import io
import json
import os
import resource
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check():
    os.environ.update(
        HF_HUB_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        HF_DATASETS_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
    )
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("Unapproved runtime network/resource download")

    started = time.monotonic()
    log = io.StringIO()
    with (
        patch("socket.socket.connect", deny),
        patch("socket.socket.connect_ex", deny),
        patch("requests.sessions.Session.request", deny),
        patch("httpx.Client.send", deny),
        patch("httpx.AsyncClient.send", deny),
        patch("nltk.download", deny),
        contextlib.redirect_stdout(log),
        contextlib.redirect_stderr(log),
    ):
        import nltk

        module = importlib.import_module(
            "generation.core_pipelines.factual_generation_individual.factual_generation"
        )
    if attempts:
        raise RuntimeError("Native import attempted runtime provisioning")
    function = module.generate_factual_qa_dataset
    assert inspect.iscoroutinefunction(function)
    signature = inspect.signature(function)
    assert signature.parameters["anonymize"].default is False
    for key in [
        "cost_per_million_small_input",
        "cost_per_million_small_output",
        "cost_per_million_large_input",
        "cost_per_million_large_output",
    ]:
        assert signature.parameters[key].default is inspect.Parameter.empty
    assets = []
    try:
        nltk.data.find("tokenizers/punkt_tab/english/")
    except LookupError:
        assets.append(
            "nltk:punkt_tab/english (must be provisioned and hashed at worker build)"
        )
    return {
        "native_import": "PASS",
        "pipeline_id": "factual-gen-indiv-pipeline",
        "runtime_network_attempts": len(attempts),
        "runtime_downloads": 0,
        "missing_assets": assets,
        "seconds": round(time.monotonic() - started, 3),
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "real_generation": "NOT_RUN",
        "hf_jobs_launched": 0,
        "provider_calls": 0,
        "ready_for_live_generation": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = check()
    except Exception as error:  # noqa: BLE001 -- no raw import/request/provider secrets in evidence
        result = {
            "native_import": "FAIL",
            "error_type": type(error).__name__,
            "missing_module": getattr(error, "name", None),
            "real_generation": "NOT_RUN",
            "ready_for_live_generation": False,
        }
    args.evidence.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    if result["native_import"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
