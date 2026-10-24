"""Three-role Dataset lifecycle, real preparation, mocked Hub I/O only."""

import json
import subprocess
import sys
import time

import pytest
from dataset_fake import manager
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api
import pilot_prepare as preparation
import pilot_storage as storage
from augmentoolkit.hf_persistence.dataset_commit import (
    DatasetConflict,
    DatasetIntegrityError,
)
from augmentoolkit.hf_persistence.hf_manager import HFHubError

AUTH = {"Authorization": "Bearer synthetic"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    for name in ["inputs", "external_configs"]:
        (root / name).mkdir(mode=0o700)
    (root / "inputs/source ü.txt").write_bytes(b"Public synthetic D0.\r\n" * 40)
    (root / "external_configs/private.yaml").write_bytes(
        b"preparation: {chunk_chars: 256}"
    )
    hub = manager(tmp_path / "mock-hub")
    hub.api.initialize([hub.source_repo, hub.work_repo, hub.output_repo])
    monkeypatch.setenv("ATK_DATA_ROOT", str(root))
    monkeypatch.setenv("ATK_PREPARATION_ENABLED", "true")
    monkeypatch.setenv("ATK_DATASET_PERSISTENCE_ENABLED", "true")
    monkeypatch.setenv("ATK_DATASET_PERSISTENCE_WRITE_APPROVED", "true")
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("WORKER_TOKEN", "synthetic")
    monkeypatch.setattr(api, "INPUTS_DIR", root / "inputs")
    monkeypatch.setattr(api, "CONFIGS_DIR", root / "external_configs")
    monkeypatch.setattr(preparation, "SERVICE", None)
    monkeypatch.setattr(storage, "HFHubManager", lambda: hub)
    import augmentoolkit.hf_persistence.dataset_commit as commits

    monkeypatch.setattr(commits, "hf_hub_download", hub.api.download)
    yield TestClient(api.app), root, hub
    for _ in range(500):
        if preparation.SERVICE is None or preparation.SERVICE.active is None:
            break
        time.sleep(0.01)


def submit(c):
    r = c.post(
        "/pilot/preparation",
        headers=AUTH,
        json={
            "source_path": "source ü.txt",
            "config_path": "private.yaml",
            "sensitivity": "D0",
        },
    )
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def terminal(c, job):
    for _ in range(500):
        response = c.get("/pilot/preparation/" + job, headers=AUTH)
        assert response.status_code == 200, response.text
        state = response.json()
        if state["status"] in {"COMPLETED", "FAILED", "PERSISTENCE_FAILED"}:
            return state
        time.sleep(0.01)
    pytest.fail("Preparation did not reach a terminal state")


def test_three_repositories_verified_and_fresh_process_recovery(setup, tmp_path):
    c, root, hub = setup
    job = submit(c)
    state = terminal(c, job)
    assert state["status"] == "COMPLETED", state
    assert state["durability"] == "VERIFIED"
    receipt = state["remote_receipt"]
    assert [call[0] for call in hub.api.calls] == [
        hub.source_repo,
        hub.work_repo,
        hub.output_repo,
        hub.work_repo,
    ]
    assert [len(call[2]) for call in hub.api.calls] == [1, 2, 2, 2]
    for label, ref in receipt["assets"].items():
        content = hub.read_dataset_verified(
            ref["repository"],
            ref["path"],
            ref["revision"],
            ref["sha256"],
            root / "hf_dataset_cache",
        )
        if label == "source":
            assert content == (root / "inputs/source ü.txt").read_bytes()
        if label == "config":
            assert content == (root / "external_configs/private.yaml").read_bytes()
    assert not (root / "pilot_state" / job / "source.txt").exists()
    fresh = tmp_path / "fresh"
    fresh.mkdir(mode=0o700)
    code = r"""
import hashlib, os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/'tests/parent'))
from dataset_fake import manager
import augmentoolkit.hf_persistence.dataset_commit as commits
from pilot_storage import DatasetLifecycle
hub=manager(Path(sys.argv[1])); commits.hf_hub_download=hub.api.download
DatasetLifecycle(hub).recover(sys.argv[2],sys.argv[3],sys.argv[4])
os.environ['ATK_DATASET_PERSISTENCE_ENABLED']='false'
from api import app
from fastapi import HTTPException
from fastapi.testclient import TestClient
c=TestClient(app);h={'Authorization':'Bearer synthetic'}
r=c.get('/pilot/preparation/'+sys.argv[2]+'/download/records.jsonl',headers=h)
assert r.status_code==200,r.text
cfg=c.get('/configs/content/'+sys.argv[2]+'.yaml',headers=h)
assert cfg.status_code==200
assert cfg.headers['etag']=='"'+hashlib.sha256(cfg.content).hexdigest()+'"'
print(__import__('json').dumps({'records_sha256':hashlib.sha256(r.content).hexdigest(),'config_sha256':hashlib.sha256(cfg.content).hexdigest(),'config_etag':cfg.headers['etag']}))
"""
    import os

    env = {**os.environ, "ATK_DATA_ROOT": str(fresh)}
    result = subprocess.check_output(
        [
            sys.executable,
            "-c",
            code,
            str(hub.api.root),
            job,
            receipt["final_work_revision"],
            receipt["manifest_sha256"],
        ],
        env=env,
        text=True,
    )
    recovered = json.loads(result)
    assert (
        recovered["records_sha256"]
        == receipt["assets"]["output:records.jsonl"]["sha256"]
    )
    assert recovered["config_sha256"] == state["config_revision"]
    assert recovered["config_etag"] == '"' + state["config_revision"] + '"'
    assert (fresh / "external_configs" / (job + ".yaml")).read_bytes() == (
        root / "external_configs/private.yaml"
    ).read_bytes()
    assert (fresh / "inputs" / (job + ".txt")).read_bytes() == (
        root / "inputs/source ü.txt"
    ).read_bytes()


@pytest.mark.parametrize("failure", ["before", "lost_ack"])
def test_interrupted_source_upload_retries_same_ids(setup, failure):
    c, _root, hub = setup
    hub.api.failure = failure
    job = submit(c)
    state = terminal(c, job)
    assert (
        state["status"] == "PERSISTENCE_FAILED" and state["durability"] == "UNVERIFIED"
    )
    assert "secret" not in json.dumps(state)
    assert (
        c.get(
            f"/pilot/preparation/{job}/download/records.jsonl", headers=AUTH
        ).status_code
        == 409
    )
    first_paths = hub.api.calls[0][2]
    r = c.post(f"/pilot/preparation/{job}/retry-persistence", headers=AUTH)
    assert r.status_code == 202
    assert terminal(c, job)["status"] == "COMPLETED"
    source_calls = [call for call in hub.api.calls if call[0] == hub.source_repo]
    assert all(call[2] == first_paths for call in source_calls)
    if failure == "lost_ack":
        assert (
            len(source_calls) == 1
        )  # recovered exact existing batch; no duplicate write


def test_changed_head_is_not_silently_rebased(setup):
    c, _root, hub = setup
    hub.api.failure = "before"
    job = submit(c)
    assert terminal(c, job)["status"] == "PERSISTENCE_FAILED"
    # A separate actor changes the source head without publishing our batch.
    hub.api.create_commit(
        repo_id=hub.source_repo,
        repo_type="dataset",
        parent_commit="0" * 40,
        operations=[],
    )
    assert (
        c.post(f"/pilot/preparation/{job}/retry-persistence", headers=AUTH).status_code
        == 202
    )
    state = terminal(c, job)
    assert state["status"] == "PERSISTENCE_FAILED"
    assert not any(call[0] == hub.output_repo for call in hub.api.calls)


@pytest.mark.parametrize("role", ["source_repo", "work_repo", "output_repo"])
def test_missing_or_public_repository_fails_closed(setup, role):
    c, _root, hub = setup
    directory = hub.api.directory(getattr(hub, role))
    data = json.loads((directory / "head.json").read_bytes())
    data["private"] = False
    (directory / "head.json").write_text(json.dumps(data))
    job = submit(c)
    state = terminal(c, job)
    assert state["status"] == "PERSISTENCE_FAILED"
    assert hub.api.calls == []


def test_corrupted_or_missing_download_rejected(setup, tmp_path):
    c, root, hub = setup
    job = submit(c)
    state = terminal(c, job)
    receipt = state["remote_receipt"]
    hub.api.corrupt = True
    with pytest.raises(DatasetIntegrityError):
        storage.DatasetLifecycle(hub).recover(
            job, receipt["final_work_revision"], receipt["manifest_sha256"]
        )
    hub.api.corrupt = False
    ref = receipt["assets"]["output:records.jsonl"]
    (hub.api.directory(ref["repository"]) / ref["revision"] / ref["path"]).unlink()
    with pytest.raises(HFHubError):
        hub.read_dataset_verified(
            ref["repository"],
            ref["path"],
            ref["revision"],
            ref["sha256"],
            root / "hf_dataset_cache",
        )


def test_approval_and_offline_configuration_guards(monkeypatch):
    monkeypatch.setenv("ATK_DATASET_PERSISTENCE_ENABLED", "true")
    monkeypatch.delenv("ATK_DATASET_PERSISTENCE_WRITE_APPROVED", raising=False)
    with pytest.raises(HTTPException) as error:
        storage.configured()
    assert error.value.status_code == 503
    monkeypatch.setenv("ATK_DATASET_PERSISTENCE_WRITE_APPROVED", "true")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(HTTPException) as error:
        storage.configured()
    assert error.value.status_code == 503


@pytest.mark.parametrize("stage", ["output", "final"])
@pytest.mark.parametrize("failure", ["before", "lost_ack"])
def test_output_and_final_manifest_are_required_before_completed(setup, stage, failure):
    from unittest.mock import patch

    c, root, hub = setup
    hub.api.failure = failure
    hub.api.failure_repo = hub.output_repo if stage == "output" else hub.work_repo
    hub.api.failure_path = None if stage == "output" else "/completed/"
    original = preparation.subprocess.Popen
    with patch("pilot_prepare.subprocess.Popen", wraps=original) as spawn:
        job = submit(c)
        state = terminal(c, job)
        assert state["status"] == "PERSISTENCE_FAILED"
        assert state["durability"] == "UNVERIFIED"
        assert (root / "outputs/preparation" / job / "records.jsonl").exists()
        assert (
            c.get(
                f"/pilot/preparation/{job}/download/records.jsonl", headers=AUTH
            ).status_code
            == 409
        )
        assert spawn.call_count == 1
        assert (
            c.post(
                f"/pilot/preparation/{job}/retry-persistence", headers=AUTH
            ).status_code
            == 202
        )
        state = terminal(c, job)
        assert state["status"] == "COMPLETED", state
        assert state["durability"] == "VERIFIED"
        assert (
            spawn.call_count == 1
        )  # retry does not regenerate already computed records
    receipt = state["remote_receipt"]
    document = hub.read_dataset_verified(
        hub.work_repo,
        receipt["manifest_path"],
        receipt["final_work_revision"],
        receipt["manifest_sha256"],
        root / "hf_dataset_cache",
    )
    doc = json.loads(document)
    assert doc["job_manifest"]["status"] == "COMPLETED"
    assert doc["assets"]["config"]["revision"] == receipt["initial_work_revision"]
    if failure == "lost_ack":
        assert any(c["reconciled"] for c in receipt["commits"])


def test_interrupted_remote_job_recovers_without_execution(
    setup, tmp_path, monkeypatch
):
    from unittest.mock import patch

    c, root, hub = setup
    service = preparation.service()
    # Hold supervision: persist the same snapshots normally used by a real child.
    with patch("pilot_prepare.threading.Thread"):
        job = submit(c)
    directory = root / "pilot_state" / job
    state = preparation.load_state(directory)
    receipt = service.persistence.prepare(state, directory)
    fresh = tmp_path / "interrupted-runtime"
    fresh.mkdir(mode=0o700)
    monkeypatch.setenv("ATK_DATA_ROOT", str(fresh))
    with patch("pilot_prepare.subprocess.Popen") as spawn:
        recovered = storage.DatasetLifecycle(hub).recover(
            job, receipt["initial_work_revision"]
        )
    assert recovered["status"] == "FAILED" and recovered["phase"] == "interrupted"
    assert recovered["durability"] == "INTERRUPTED"
    spawn.assert_not_called()
    assert not (fresh / "outputs/preparation" / job / "records.jsonl").exists()
    service.active = None


def test_recovery_write_error_leaves_no_partial_publication(
    setup, tmp_path, monkeypatch
):
    from unittest.mock import patch

    c, _root, hub = setup
    job = submit(c)
    state = terminal(c, job)
    receipt = state["remote_receipt"]
    fresh = tmp_path / "disk-error"
    fresh.mkdir(mode=0o700)
    monkeypatch.setenv("ATK_DATA_ROOT", str(fresh))
    lifecycle = storage.DatasetLifecycle(hub)
    with (
        patch("pilot_storage.os.fsync", side_effect=OSError("synthetic disk full")),
        pytest.raises(OSError),
    ):
        lifecycle.recover(
            job, receipt["final_work_revision"], receipt["manifest_sha256"]
        )
    assert not (fresh / "pilot_state" / job).exists()
    assert not list((fresh / "inputs").iterdir())
    assert not list((fresh / "external_configs").iterdir())
    assert not (fresh / "outputs/preparation" / job).exists()
    assert not list((fresh / "file_staging").iterdir())


@pytest.mark.parametrize("classification", [None, "D1", "D2"])
def test_classification_rejection_before_any_hub_io(setup, classification):
    from unittest.mock import patch

    c, _root, hub = setup
    with patch("pilot_storage.HFHubManager", return_value=hub) as factory:
        response = c.post(
            "/pilot/preparation",
            headers=AUTH,
            json={
                "source_path": "source ü.txt",
                "config_path": "private.yaml",
                "sensitivity": classification,
            },
        )
    assert response.status_code == 400
    factory.assert_not_called()
    assert hub.api.calls == []


def test_readonly_preflight_never_creates_repositories(monkeypatch):
    from unittest.mock import patch

    monkeypatch.setenv("ATK_DATASET_PERSISTENCE_ENABLED", "true")
    monkeypatch.setenv("ATK_DATASET_PERSISTENCE_WRITE_APPROVED", "true")
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    for name in ["HF_SOURCE_REPO", "HF_WORK_REPO", "HF_OUTPUT_REPO"]:
        monkeypatch.delenv(name, raising=False)
    with (
        patch("augmentoolkit.hf_persistence.hf_manager.HfApi") as sdk,
        pytest.raises(HTTPException) as error,
    ):
        storage.configured()
    assert error.value.status_code == 503
    sdk.return_value.repo_info.assert_not_called()
    sdk.return_value.create_repo.assert_not_called()
    sdk.return_value.create_commit.assert_not_called()


@pytest.mark.parametrize("role", ["source_repo", "work_repo", "output_repo"])
def test_missing_dataset_repositories_are_not_created(setup, role):
    c, _root, hub = setup
    (hub.api.directory(getattr(hub, role)) / "head.json").unlink()
    job = submit(c)
    assert terminal(c, job)["status"] == "PERSISTENCE_FAILED"
    assert hub.api.calls == []


def test_invalid_credentials_fail_closed(setup, monkeypatch):
    c, _root, hub = setup

    def denied(**kwargs):
        raise PermissionError("credential-value-must-not-leak")

    monkeypatch.setattr(hub.api, "repo_info", denied)
    job = submit(c)
    state = terminal(c, job)
    assert state["status"] == "PERSISTENCE_FAILED"
    assert "credential-value" not in json.dumps(state)
    assert hub.api.calls == []


def test_explicit_token_binding_required(setup):
    c, _root, hub = setup
    hub.token = None
    job = submit(c)
    assert terminal(c, job)["status"] == "PERSISTENCE_FAILED"
    assert hub.api.calls == []


def test_recovery_conflict_never_overwrites_existing_input(
    setup, tmp_path, monkeypatch
):
    c, _root, hub = setup
    job = submit(c)
    state = terminal(c, job)
    receipt = state["remote_receipt"]
    fresh = tmp_path / "conflicting-runtime"
    fresh.mkdir(mode=0o700)
    (fresh / "inputs").mkdir(mode=0o700)
    target = fresh / "inputs" / f"{job}.txt"
    target.write_bytes(b"existing private file")
    monkeypatch.setenv("ATK_DATA_ROOT", str(fresh))
    with pytest.raises(HFHubError, match="conflict"):
        storage.DatasetLifecycle(hub).recover(
            job, receipt["final_work_revision"], receipt["manifest_sha256"]
        )
    assert target.read_bytes() == b"existing private file"
    assert not (fresh / "pilot_state" / job).exists()
    assert not list((fresh / "external_configs").iterdir())


def test_commit_requires_parent_and_rejects_wrong_immutable_bytes(setup):
    _c, root, hub = setup
    with pytest.raises(HFHubError):
        hub.commit_dataset_verified(
            hub.source_repo,
            None,
            {"inputs/synthetic.txt": b"Public D0"},
            root / "hf_dataset_cache",
        )
    assert hub.api.calls == []
    first = hub.commit_dataset_verified(
        hub.source_repo,
        "0" * 40,
        {"inputs/synthetic.txt": b"Public D0"},
        root / "hf_dataset_cache",
    )
    with pytest.raises(DatasetIntegrityError):
        hub.commit_dataset_verified(
            hub.source_repo,
            first["revision"],
            {"inputs/synthetic.txt": b"different"},
            root / "hf_dataset_cache",
        )
    assert len(hub.api.calls) == 1
    again = hub.commit_dataset_verified(
        hub.source_repo,
        first["revision"],
        {"inputs/synthetic.txt": b"Public D0"},
        root / "hf_dataset_cache",
    )
    assert again["revision"] == first["revision"] and again["reconciled"]
    assert len(hub.api.calls) == 1


def test_unapproved_repository_and_endpoint_rejected(setup):
    _c, _root, hub = setup
    with pytest.raises(HFHubError):
        hub.private_dataset_head("not-configured/destination")
    hub.api.endpoint = "https://unapproved.invalid"
    with pytest.raises(HFHubError):
        hub.private_dataset_head(hub.source_repo)
    assert hub.api.calls == []


def test_server_side_parent_commit_conflict_is_typed_and_sanitized(setup, monkeypatch):
    from huggingface_hub.utils import HfHubHTTPError
    from requests import Response

    _c, root, hub = setup
    response = Response()
    response.status_code = 409

    def conflict(**kwargs):
        raise HfHubHTTPError("secret-signed-url-must-not-leak", response=response)

    monkeypatch.setattr(hub.api, "create_commit", conflict)
    with pytest.raises(DatasetConflict) as error:
        hub.commit_dataset_verified(
            hub.source_repo,
            "0" * 40,
            {"inputs/synthetic.txt": b"Public D0"},
            root / "hf_dataset_cache",
        )
    assert "secret" not in str(error.value)


def test_child_imports_trusted_source_not_callers_working_directory(
    setup, tmp_path, monkeypatch
):
    c, _root, _hub = setup
    untrusted = tmp_path / "untrusted-cwd"
    untrusted.mkdir()
    (untrusted / "pilot_prepare.py").write_text(
        "raise RuntimeError('untrusted module must never execute')\n"
    )
    monkeypatch.chdir(untrusted)
    job = submit(c)
    state = terminal(c, job)
    assert state["status"] == "COMPLETED", state
    assert state["durability"] == "VERIFIED"
