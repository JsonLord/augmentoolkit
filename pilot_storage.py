"""Three private Dataset repositories; local disk is temporary working storage."""

import json
import os
from pathlib import Path

import httpx
from fastapi import HTTPException
from requests.exceptions import RequestException

from app_storage import data_directory
from augmentoolkit.hf_persistence.dataset_commit import digest
from augmentoolkit.hf_persistence.hf_manager import HFHubError, HFHubManager
from augmentoolkit.hf_persistence.job_manifest import JobManifest


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def validate_outputs(state, source, config, data, raw_manifest):
    """Reuse the deterministic preparer; reject malformed or substituted records."""
    from pilot_prepare import records

    try:
        rows = [json.loads(line) for line in data.splitlines()]
        manifest = json.loads(raw_manifest)
        expected = records(source, config, state["source_filename"])
        if (
            rows != expected
            or type(state["record_count"]) is not int
            or state["record_count"] != len(rows)
        ):
            raise ValueError
        if any(
            type(row.get(key)) is not int
            for row in rows
            for key in ["index", "normalized_start", "normalized_end"]
        ):
            raise ValueError
        if (
            manifest["kind"] != "deterministic_preparation"
            or manifest["sensitivity"] != "D0"
            or manifest["job_id"] != state["job_id"]
            or manifest["source_filename"] != state["source_filename"]
            or manifest["source_sha256"] != state["source_sha256"]
            or manifest["config_revision"] != state["config_revision"]
            or manifest["records_sha256"] != digest(data)
            or type(manifest["record_count"]) is not int
            or manifest["record_count"] != len(rows)
            or manifest["source_bytes"] != len(source)
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, HTTPException):
        raise HFHubError("Preparation output schema/provenance invalid") from None


def configured():
    if os.environ.get("ATK_DATASET_PERSISTENCE_ENABLED", "false").lower() != "true":
        return None
    if os.environ.get("ATK_DATASET_PERSISTENCE_WRITE_APPROVED") != "true":
        raise HTTPException(503, "Dataset persistence requires operator approval")
    if os.environ.get("HF_HUB_OFFLINE", "0").lower() in {"1", "true"}:
        raise HTTPException(503, "Dataset storage requires HF_HUB_OFFLINE=0")
    manager = HFHubManager()
    if not all([manager.source_repo, manager.work_repo, manager.output_repo]):
        raise HTTPException(
            503, "HF_SOURCE_REPO, HF_WORK_REPO and HF_OUTPUT_REPO are required"
        )
    return DatasetLifecycle(manager)


class DatasetLifecycle:
    def __init__(self, manager):
        self.hub = manager
        self.cache = data_directory("hf_dataset_cache")
        self.roles = {
            "source": manager.source_repo,
            "work": manager.work_repo,
            "output": manager.output_repo,
        }
        if not all(self.roles.values()):
            raise HFHubError("All Dataset repository roles must be configured")

    def receipt(self, directory):
        path = directory / "receipt.json"
        if path.exists():
            value = json.loads(path.read_bytes())
            if value["repositories"] != self.roles:
                raise HFHubError("Dataset repository binding changed")
            return value
        return {
            "repositories": self.roles,
            "heads": {
                repo: self.hub.private_dataset_head(repo)
                for repo in set(self.roles.values())
            },
            "assets": {},
            "commits": [],
        }

    def commit(self, directory, receipt, role, files, mutable=()):
        from pilot_prepare import write_json

        repo = self.roles[role]
        # Persist expected parent and immutable intent before the network call.
        write_json(directory / "receipt.json", receipt)
        result = self.hub.commit_dataset_verified(
            repo, receipt["heads"][repo], files, self.cache, mutable
        )
        receipt["heads"][repo] = result["revision"]
        receipt["commits"].append({"role": role, **result})
        write_json(directory / "receipt.json", receipt)
        return result["revision"]

    def document(self, state, assets, status):
        job = JobManifest(
            job_id=state["job_id"],
            pipeline="pilot-txt-preparation",
            source_repo=self.roles["source"],
            work_repo=self.roles["work"],
            output_repo=self.roles["output"],
            source_revision=assets["source"]["revision"],
            source_path=assets["source"]["path"],
            configuration_hash=state["config_revision"],
            code_revision=state.get("code_revision", "unknown"),
            current_stage="persisted" if status == "COMPLETED" else "preparation",
            output_schema_version="preparation-chunks-v1",
            status=status,
            timestamps={
                "created_at": state["created_at"],
                "updated_at": state["created_at"],
                "completed_at": None,
            },
            output_shards=[v for k, v in assets.items() if k.startswith("output:")],
            final_output_revision=next(
                (v["revision"] for k, v in assets.items() if k.startswith("output:")),
                None,
            ),
        )
        safe_state = {
            k: v
            for k, v in state.items()
            if k
            not in {
                "remote_receipt",
                "error",
                "persistence_error",
                "persistence_error_code",
            }
        }
        safe_state.update(
            status=status,
            phase="ready" if status == "COMPLETED" else "preparation",
            durability="VERIFIED" if status == "COMPLETED" else "PENDING",
        )
        return {
            "schema_version": 1,
            "kind": "pilot-dataset-lifecycle",
            "sensitivity": "D0",
            "repositories": self.roles,
            "job_manifest": job.to_dict(),
            "preparation_state": safe_state,
            "assets": assets,
        }

    def prepare(self, state, directory):
        from pilot_prepare import write_json

        if state["sensitivity"] != "D0":
            raise HFHubError("Only explicit D0 preparation may be persisted")
        for repo in set(self.roles.values()):
            self.hub.private_dataset_head(repo)
        receipt = self.receipt(directory)
        raw, cfg = (
            (directory / "source.txt").read_bytes(),
            (directory / "config.yaml").read_bytes(),
        )
        if (
            digest(raw) != state["source_sha256"]
            or digest(cfg) != state["config_revision"]
        ):
            raise HFHubError("Preparation snapshots changed")
        job_id = state["job_id"]
        source_path = f"inputs/{job_id}/{digest(raw)}.txt"
        config_path = f"configs/{job_id}/{digest(cfg)}.yaml"
        if "source" not in receipt["assets"]:
            revision = self.commit(directory, receipt, "source", {source_path: raw})
            receipt["assets"]["source"] = {
                "repository": self.roles["source"],
                "path": source_path,
                "revision": revision,
                "sha256": digest(raw),
            }
            write_json(directory / "receipt.json", receipt)
        if "config" not in receipt["assets"]:
            doc_assets = {
                **receipt["assets"],
                "config": {
                    "repository": self.roles["work"],
                    "path": config_path,
                    "sha256": digest(cfg),
                },
            }
            document = self.document(state, doc_assets, "RUNNING")
            canonical = f"jobs/{job_id}/preparation.json"
            revision = self.commit(
                directory,
                receipt,
                "work",
                {config_path: cfg, canonical: encoded(document)},
                mutable=(canonical,),
            )
            receipt["assets"]["config"] = {**doc_assets["config"], "revision": revision}
            receipt["initial_work_revision"] = revision
            write_json(directory / "receipt.json", receipt)
        return receipt

    def finish(self, state, directory, output):
        from pilot_prepare import ARTIFACTS, write_json

        for repo in set(self.roles.values()):
            self.hub.private_dataset_head(repo)
        receipt = self.receipt(directory)
        if not {"source", "config"} <= set(receipt["assets"]):
            raise HFHubError("Initial durable input/configuration receipt missing")
        files = {}
        for name in sorted(ARTIFACTS):
            data = (output / name).read_bytes()
            if digest(data) != state["artifact_hashes"][name]:
                raise HFHubError("Local output checksum mismatch")
            files[f"outputs/{state['job_id']}/{digest(data)}/{name}"] = data
        for row in (
            json.loads(line)
            for line in (output / "records.jsonl").read_bytes().splitlines()
        ):
            if (
                row.get("sensitivity") != "D0"
                or row.get("source_sha256") != state["source_sha256"]
                or row.get("config_revision") != state["config_revision"]
            ):
                raise HFHubError("Output schema/provenance mismatch")
        validate_outputs(
            state,
            (directory / "source.txt").read_bytes(),
            (directory / "config.yaml").read_bytes(),
            (output / "records.jsonl").read_bytes(),
            (output / "manifest.json").read_bytes(),
        )
        if not any(k.startswith("output:") for k in receipt["assets"]):
            revision = self.commit(directory, receipt, "output", files)
            for path, data in files.items():
                name = Path(path).name
                receipt["assets"]["output:" + name] = {
                    "repository": self.roles["output"],
                    "path": path,
                    "revision": revision,
                    "sha256": digest(data),
                }
            write_json(directory / "receipt.json", receipt)
        document = self.document(state, receipt["assets"], "COMPLETED")
        raw = encoded(document)
        canonical = f"jobs/{state['job_id']}/preparation.json"
        immutable = f"jobs/{state['job_id']}/completed/{digest(raw)}.json"
        revision = self.commit(
            directory,
            receipt,
            "work",
            {canonical: raw, immutable: raw},
            mutable=(canonical,),
        )
        receipt.update(
            final_work_revision=revision,
            manifest_sha256=digest(raw),
            manifest_path=canonical,
        )
        write_json(directory / "receipt.json", receipt)
        return receipt

    def recover(self, job_id, revision, expected=None):
        """Pinned, read-only recovery. Publish state last; roll back partial files."""
        import shutil
        import tempfile

        from pilot_prepare import identifier, parameters, write_json

        identifier(job_id)
        raw = self.hub.read_dataset_verified(
            self.roles["work"],
            f"jobs/{job_id}/preparation.json",
            revision,
            expected,
            self.cache,
        )
        try:
            doc = json.loads(raw)
            state = dict(doc["preparation_state"])
            job = doc["job_manifest"]
            assets = doc["assets"]
            if (
                doc["schema_version"] != 1
                or doc["kind"] != "pilot-dataset-lifecycle"
                or doc["sensitivity"] != "D0"
                or doc["repositories"] != self.roles
                or job["job_id"] != job_id
                or job["pipeline"] != "pilot-txt-preparation"
                or state["job_id"] != job_id
                or state["sensitivity"] != "D0"
            ):
                raise ValueError
            allowed_state = {
                "job_id",
                "status",
                "phase",
                "sensitivity",
                "source_filename",
                "source_sha256",
                "config_revision",
                "record_count",
                "created_at",
                "code_revision",
                "durability",
                "artifact_hashes",
            }
            if set(state) - allowed_state or not {"source", "config"} <= set(assets):
                raise ValueError
            if set(assets) - {
                "source",
                "config",
                "output:records.jsonl",
                "output:manifest.json",
            }:
                raise ValueError
            if (
                assets["source"]["sha256"] != state["source_sha256"]
                or assets["config"]["sha256"] != state["config_revision"]
            ):
                raise ValueError
            if (
                job["source_repo"] != self.roles["source"]
                or job["work_repo"] != self.roles["work"]
                or job["output_repo"] != self.roles["output"]
                or job["source_revision"] != assets["source"]["revision"]
                or job["source_path"] != assets["source"]["path"]
                or job["configuration_hash"] != state["config_revision"]
            ):
                raise ValueError
            if job["status"] == "COMPLETED" and (
                job["final_output_revision"]
                != assets["output:records.jsonl"]["revision"]
                or assets["output:manifest.json"]["revision"]
                != job["final_output_revision"]
                or not assets["config"].get("revision")
            ):
                raise ValueError
            if job["status"] == "COMPLETED" and set(assets) != {
                "source",
                "config",
                "output:records.jsonl",
                "output:manifest.json",
            }:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise HFHubError("Durable job manifest binding mismatch") from None
        directory = data_directory("pilot_state") / job_id
        if directory.exists() or directory.is_symlink():
            raise HFHubError("Recovery target already exists")
        restored = {}
        for label, ref in assets.items():
            role = (
                "source"
                if label == "source"
                else "work"
                if label == "config"
                else "output"
            )
            prefix = (
                f"inputs/{job_id}/"
                if role == "source"
                else f"configs/{job_id}/"
                if role == "work"
                else f"outputs/{job_id}/"
            )
            expected_path = (
                prefix
                + ref["sha256"]
                + (
                    ".txt"
                    if role == "source"
                    else ".yaml"
                    if role == "work"
                    else "/" + label.split(":", 1)[1]
                )
            )
            if ref["repository"] != self.roles[role] or ref["path"] != expected_path:
                raise HFHubError("Unexpected Dataset object binding")
            restored[label] = self.hub.read_dataset_verified(
                ref["repository"],
                ref["path"],
                ref.get("revision", revision),
                ref["sha256"],
                self.cache,
            )
        try:
            restored["source"].decode("utf-8-sig")
            parameters(restored["config"])
            if job["status"] == "COMPLETED":
                manifest = json.loads(restored["output:manifest.json"])
                rows = [
                    json.loads(line)
                    for line in restored["output:records.jsonl"].splitlines()
                ]
                if (
                    manifest["job_id"] != job_id
                    or manifest["source_sha256"] != state["source_sha256"]
                    or manifest["config_revision"] != state["config_revision"]
                    or manifest["records_sha256"]
                    != digest(restored["output:records.jsonl"])
                    or len(rows) != state["record_count"]
                ):
                    raise ValueError
                for name in ["records.jsonl", "manifest.json"]:
                    if (
                        digest(restored["output:" + name])
                        != state["artifact_hashes"][name]
                    ):
                        raise ValueError
                if not rows or any(
                    r.get("sensitivity") != "D0"
                    or r.get("source_sha256") != state["source_sha256"]
                    or r.get("config_revision") != state["config_revision"]
                    for r in rows
                ):
                    raise ValueError
        except (KeyError, TypeError, ValueError, HTTPException):
            raise HFHubError("Recovered content/provenance invalid") from None
        if job["status"] == "COMPLETED":
            validate_outputs(
                state,
                restored["source"],
                restored["config"],
                restored["output:records.jsonl"],
                restored["output:manifest.json"],
            )
        inputs, configs = data_directory("inputs"), data_directory("external_configs")
        output = data_directory("outputs/preparation") / job_id
        plans = [
            (inputs / f"{job_id}.txt", restored["source"]),
            (configs / f"{job_id}.yaml", restored["config"]),
        ]
        if job["status"] == "COMPLETED":
            plans += [
                (output / name, restored["output:" + name])
                for name in ["records.jsonl", "manifest.json"]
            ]
        if (
            output.exists()
            or output.is_symlink()
            or any(p.exists() or p.is_symlink() for p, _ in plans)
        ):
            raise HFHubError("Recovery destination conflict")
        directory.mkdir(mode=0o700)
        published = []
        try:
            if job["status"] == "COMPLETED":
                output.mkdir(mode=0o700)
            for path, data in plans:
                fd, temporary = tempfile.mkstemp(
                    prefix="recover-", dir=data_directory("file_staging")
                )
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary, path)  # atomic no-overwrite, including races
                    published.append(path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            if job["status"] == "COMPLETED":
                state.update(status="COMPLETED", phase="ready", durability="VERIFIED")
            else:
                state.update(
                    status="FAILED",
                    phase="interrupted",
                    durability="INTERRUPTED",
                    error="Interrupted durable preparation; no automatic resume",
                )
            state["remote_receipt"] = {
                "final_work_revision": revision,
                "manifest_sha256": digest(raw),
                "assets": assets,
            }
            write_json(directory / "state.json", state)
        except Exception:
            for path in published:
                path.unlink(missing_ok=True)
            shutil.rmtree(directory, ignore_errors=True)
            if output.exists():
                shutil.rmtree(output, ignore_errors=True)
            raise
        return state

    def discover(self):
        revision = self.hub.private_dataset_head(self.roles["work"])
        try:
            files = self.hub.api.list_repo_files(
                repo_id=self.roles["work"],
                repo_type="dataset",
                revision=revision,
                token=self.hub.token,
            )
        except (OSError, ValueError, RequestException, httpx.HTTPError):
            raise HFHubError("Durable job discovery failed") from None
        return revision, sorted(
            {
                p.split("/")[1]
                for p in files
                if p.startswith("jobs/")
                and len(p.split("/")) == 3
                and p.endswith("/preparation.json")
            }
        )
