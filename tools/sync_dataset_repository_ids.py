"""Sync only the three explicitly authorized repository-ID variables, never secrets."""

import argparse
import json
import os
from pathlib import Path

from huggingface_hub import HfApi

KEYS = ("HF_SOURCE_REPO", "HF_WORK_REPO", "HF_OUTPUT_REPO")
TARGET = "Leon4gr45/augment"
EXPECTED_REVISION = "d26a7807f1a46c44edb51d81d8afec081c6a78a8"
PRESERVE = (
    "WORKER_TOKEN",
    "HF_HUB_OFFLINE",
    "ATK_PREPARATION_ENABLED",
    "ATK_DATASET_PERSISTENCE_ENABLED",
    "ATK_DATASET_PERSISTENCE_WRITE_APPROVED",
    "ATK_JOB_EXECUTION_ENABLED",
    "ATK_HF_RECOVERY_ENABLED",
    "HF_AUTO_RESUME",
    "OCR_ENABLE_GPU",
    "OCR_ENABLE_GPU_OVERFLOW",
    "OCR_ALLOW_EXTERNAL_CPU_D0",
    "OCR_ALLOW_EXTERNAL_CPU_D1",
    "OCR_ALLOW_EXTERNAL_CPU_D2",
)


class SyncFailed(Exception):
    def __init__(self, attempted, confirmed, error_type):
        super().__init__("Repository-ID sync incomplete")
        self.result = {
            "status": "FAILED",
            "attempted_keys": attempted,
            "confirmed_keys": confirmed,
            "error_type": error_type,
            "applied": False,
            "partial_change_possible": bool(attempted),
        }


def sync(api, roles, *, apply=False):
    if set(roles) != set(KEYS) or not all(roles.values()):
        raise ValueError("All three actual repository IDs must be configured")
    for repo in set(roles.values()):
        info = api.repo_info(repo_id=repo, repo_type="dataset")
        if info.private is not True:
            raise ValueError(
                "All Dataset repositories must already exist and be private"
            )
    space = api.space_info(TARGET)
    if not space.private or space.sha != EXPECTED_REVISION:
        raise ValueError("Space privacy/source revision changed; stop for review")
    before = api.get_space_variables(TARGET)
    preserved = {k: getattr(before.get(k), "value", None) for k in PRESERVE}
    if (
        preserved["HF_HUB_OFFLINE"] != "1"
        or preserved["ATK_JOB_EXECUTION_ENABLED"] != "false"
    ):
        raise ValueError("Expected offline/execution boundary changed")
    changes = {
        k: v for k, v in roles.items() if getattr(before.get(k), "value", None) != v
    }
    if apply:
        attempted, confirmed = [], []
        try:
            for key, value in changes.items():
                attempted.append(key)
                api.add_space_variable(repo_id=TARGET, key=key, value=value)
                confirmed.append(key)
        except Exception as error:  # noqa: BLE001 -- preserve partial progress without SDK secret strings
            raise SyncFailed(attempted, confirmed, type(error).__name__) from None
        try:
            after = api.get_space_variables(TARGET)
            info = api.space_info(TARGET)
        except Exception as error:  # noqa: BLE001 -- preserve confirmed writes on readback failure
            raise SyncFailed(attempted, confirmed, type(error).__name__) from None
        if any(
            getattr(after.get(k), "value", None) != v for k, v in roles.items()
        ) or any(
            getattr(after.get(k), "value", None) != v for k, v in preserved.items()
        ):
            raise SyncFailed(attempted, confirmed, "PostVerificationMismatch")
        if not info.private or info.sha != EXPECTED_REVISION:
            raise SyncFailed(attempted, confirmed, "PostSourceMismatch")
    return {
        "target": TARGET,
        "source_revision": EXPECTED_REVISION,
        "repository_ids": roles,
        "changed_keys": list(changes),
        "applied": apply,
        "secrets_changed": False,
        "execution_or_offline_flags_changed": False,
        "manual_restart_requested": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    # Reserve and fsync the audit file before any possible external mutation.
    with os.fdopen(
        os.open(args.evidence, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w+"
    ) as file:
        json.dump({"status": "PREPARED", "applied": False}, file)
        file.flush()
        os.fsync(file.fileno())
        try:
            roles = {k: os.environ.get(k, "") for k in KEYS}
            if not all(roles.values()):
                result = {
                    "status": "WAITING_FOR_REPOSITORY_IDS",
                    "missing": [k for k, v in roles.items() if not v],
                    "applied": False,
                }
            else:
                token = os.environ.get("HF_TOKEN")
                if not token:
                    raise ValueError("Existing secure HF_TOKEN binding required")
                result = sync(
                    HfApi(endpoint="https://huggingface.co", token=token),
                    roles,
                    apply=args.apply,
                )
        except SyncFailed as error:
            result = error.result
        except Exception as error:  # noqa: BLE001 -- never log SDK credential/URL strings
            result = {
                "status": "FAILED",
                "error_type": type(error).__name__,
                "applied": False,
            }
        file.seek(0)
        file.truncate()
        json.dump(result, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    print(json.dumps(result))
    if result.get("status") == "FAILED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
