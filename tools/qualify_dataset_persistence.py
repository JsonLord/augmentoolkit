"""One tiny synthetic trial. Default is read-only preflight, never repository creation."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def recover(args):
    os.environ["ATK_DATA_ROOT"] = str(args.root)
    from augmentoolkit.hf_persistence.hf_manager import HFHubManager
    from pilot_storage import DatasetLifecycle

    state = DatasetLifecycle(HFHubManager()).recover(
        args.recover, args.work_revision, args.manifest_sha256
    )
    os.environ["ATK_DATASET_PERSISTENCE_ENABLED"] = "false"
    os.environ["ATK_PREPARATION_ENABLED"] = "true"
    os.environ["WORKER_TOKEN"] = "synthetic-local-qualification"
    from fastapi.testclient import TestClient

    from api import app

    client = TestClient(app)
    headers = {"Authorization": "Bearer synthetic-local-qualification"}
    response = client.get(
        f"/pilot/preparation/{args.recover}/download/records.jsonl", headers=headers
    )
    assert response.status_code == 200
    config = client.get(f"/configs/content/{args.recover}.yaml", headers=headers)
    assert (
        config.status_code == 200
        and config.headers["etag"]
        == '"' + hashlib.sha256(config.content).hexdigest() + '"'
    )
    return {
        "status": state["status"],
        "records_sha256": hashlib.sha256(response.content).hexdigest(),
        "source_sha256": hashlib.sha256(
            (args.root / "inputs" / f"{args.recover}.txt").read_bytes()
        ).hexdigest(),
        "config_sha256": hashlib.sha256(config.content).hexdigest(),
        "config_etag": config.headers["etag"],
        "fresh_process": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-writes", action="store_true")
    parser.add_argument("--confirm-synthetic-d0", action="store_true")
    parser.add_argument("--source-sha")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--resume")
    parser.add_argument("--recover")
    parser.add_argument("--work-revision")
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    roles = {
        key: os.environ.get(key, "")
        for key in ["HF_SOURCE_REPO", "HF_WORK_REPO", "HF_OUTPUT_REPO"]
    }
    if not all(roles.values()):
        raise ValueError(
            "Actual Dataset repository IDs are required; none are invented"
        )
    if os.environ.get("HF_HUB_OFFLINE", "0").lower() in {"1", "true"}:
        raise ValueError(
            "Storage trial requires HF_HUB_OFFLINE=0 in this isolated process"
        )
    if args.recover:
        if (
            not args.root
            or args.root.exists()
            or not args.work_revision
            or not args.manifest_sha256
        ):
            raise ValueError("Fresh root and pinned manifest revision/hash required")
        return recover(args)
    from augmentoolkit.hf_persistence.hf_manager import HFHubManager

    hub = HFHubManager()
    if not args.execute_writes:
        return {
            "status": "PREFLIGHT_ONLY",
            "repositories": roles,
            "private_heads": {
                repo: hub.private_dataset_head(repo) for repo in roles.values()
            },
            "writes": False,
        }
    if (
        not args.confirm_synthetic_d0
        or os.environ.get("ATK_DATASET_PERSISTENCE_WRITE_APPROVED") != "true"
    ):
        raise ValueError(
            "Separate write approval and explicit synthetic D0 confirmation required"
        )
    source = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    if (
        args.source_sha != head
        or subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=source, text=True
        ).strip()
    ):
        raise ValueError("Clean reviewed source SHA required")
    if not args.root or not args.evidence:
        raise ValueError("Explicit private working root and evidence output required")
    if os.environ.get("ATK_JOB_EXECUTION_ENABLED", "false").lower() == "true":
        raise ValueError("Generic job execution must remain disabled")
    if not args.resume:
        args.root.mkdir(mode=0o700, exist_ok=False)
    os.environ["ATK_DATA_ROOT"] = str(args.root)
    os.environ["ATK_DATASET_PERSISTENCE_ENABLED"] = "true"
    os.environ["OCR_SOURCE_REVISION"] = head
    from app_storage import data_directory
    from pilot_prepare import Preparation, atomic

    service = Preparation()
    if args.resume:
        job = args.resume
        service.retry_persistence(job)
    else:
        inputs, configs = data_directory("inputs"), data_directory("external_configs")
        atomic(
            inputs / "qualification.txt",
            "Public synthetic D0 persistence.\r\nUnicode: Grüße.\n".encode(),
        )
        atomic(
            configs / "qualification.yaml",
            b"preparation: {chunk_chars: 64, overlap_chars: 0}\n",
        )
        job = service.submit(
            inputs, configs, "qualification.txt", "qualification.yaml", "D0"
        )["job_id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = service.status(job)
        if state["status"] in {"COMPLETED", "PERSISTENCE_FAILED", "FAILED"}:
            break
        time.sleep(0.1)
    else:
        raise TimeoutError("Retain the private working root and resume the same job ID")
    evidence = {
        "job_id": job,
        "source_sha": head,
        "state": state,
        "hub_mocked": False,
        "durable_storage": "NOT_QUALIFIED",
    }
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    os.chmod(args.evidence, 0o600)
    if state["status"] != "COMPLETED" or state["durability"] != "VERIFIED":
        raise RuntimeError(
            "Persistence failed; evidence/root retained for explicit retry"
        )
    receipt = state["remote_receipt"]
    fresh = args.root.with_name(args.root.name + "-fresh")
    result = subprocess.check_output(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--recover",
            job,
            "--work-revision",
            receipt["final_work_revision"],
            "--manifest-sha256",
            receipt["manifest_sha256"],
            "--root",
            str(fresh),
        ],
        text=True,
    )
    recovery = json.loads(result)
    assert (
        recovery["records_sha256"]
        == receipt["assets"]["output:records.jsonl"]["sha256"]
    )
    assert (
        recovery["source_sha256"] == state["source_sha256"]
        and recovery["config_sha256"] == state["config_revision"]
    )
    evidence.update(durable_storage="PASS", recovery=recovery)
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    return {
        "status": "PASS",
        "job_id": job,
        "evidence": str(args.evidence),
        "remote_revisions": [c["revision"] for c in receipt["commits"]],
        "space_changed": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except Exception as error:  # noqa: BLE001 -- never print SDK URLs, headers, or credentials
        print(json.dumps({"status": "FAILED", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
