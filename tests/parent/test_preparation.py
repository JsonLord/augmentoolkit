"""Real local child execution with synthetic D0; no Redis or model calls."""

import json
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api
import pilot_prepare as pilot

AUTH = {"Authorization": "Bearer synthetic"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    inputs, configs = root / "inputs", root / "external_configs"
    inputs.mkdir(mode=0o700)
    configs.mkdir(mode=0o700)
    monkeypatch.setenv("ATK_DATA_ROOT", str(root))
    monkeypatch.setenv("ATK_PREPARATION_ENABLED", "true")
    monkeypatch.setenv("WORKER_TOKEN", "synthetic")
    monkeypatch.setattr(api, "INPUTS_DIR", inputs)
    monkeypatch.setattr(api, "CONFIGS_DIR", configs)
    monkeypatch.setattr(pilot, "SERVICE", None)
    (inputs / "Synthetic ü.txt").write_bytes(("Public D0 text.\r\n" * 40).encode())
    (configs / "private.yaml").write_text(
        "preparation:\n  chunk_chars: 128\n  overlap_chars: 16\n"
    )
    yield TestClient(api.app), root
    for _ in range(100):
        if pilot.SERVICE is None or pilot.SERVICE.active is None:
            break
        time.sleep(0.02)


def submit(c, **kwargs):
    return c.post(
        "/pilot/preparation",
        headers=AUTH,
        json={
            "source_path": "Synthetic ü.txt",
            "config_path": "private.yaml",
            "sensitivity": "D0",
            **kwargs,
        },
    )


def wait(c, job):
    for _ in range(200):
        r = c.get("/pilot/preparation/" + job, headers=AUTH).json()
        if r["status"] != "RUNNING":
            return r
        time.sleep(0.02)
    pytest.fail("Child did not finish")


def test_real_child_exact_artifacts_and_restart(setup):
    c, root = setup
    r = submit(c)
    assert r.status_code == 202
    job = r.json()["job_id"]
    state = wait(c, job)
    assert state["status"] == "COMPLETED"
    data = c.get(
        f"/pilot/preparation/{job}/download/records.jsonl", headers=AUTH
    ).content
    expected = pilot.records(
        (root / "inputs/Synthetic ü.txt").read_bytes(),
        (root / "external_configs/private.yaml").read_bytes(),
        "Synthetic ü.txt",
    )
    assert [json.loads(line) for line in data.splitlines()] == expected
    assert (
        c.get(f"/pilot/preparation/{job}/preview", headers=AUTH).json()["records"]
        == expected[:10]
    )
    assert c.get(f"/pilot/preparation/{job}/outputs", headers=AUTH).json()[
        "artifacts"
    ] == ["manifest.json", "records.jsonl"]
    manifest = c.get(
        f"/pilot/preparation/{job}/download/manifest.json", headers=AUTH
    ).json()
    assert manifest["records_sha256"] == pilot.digest(data)
    assert manifest["kind"] == "deterministic_preparation"
    assert (
        root / "outputs/preparation" / job / "records.jsonl"
    ).stat().st_mode & 0o777 == 0o600
    # Fresh service reconstructs completed status; no scheduler/browser dependency.
    pilot.SERVICE = pilot.Preparation()
    assert (
        c.get(f"/pilot/preparation/{job}/download/records.jsonl", headers=AUTH).content
        == data
    )


@pytest.mark.parametrize("classification", [None, "D1", "D2", "d0"])
def test_classification(setup, classification):
    c, _ = setup
    assert submit(c, sensitivity=classification).status_code == 400
    assert c.get("/pilot/preparation", headers=AUTH).json() == []


@pytest.mark.parametrize(
    "path",
    [
        "/pilot/preparation",
        "/pilot/preparation/not-id",
        "/pilot/preparation/not-id/outputs",
        "/pilot/preparation/not-id/preview",
        "/pilot/preparation/not-id/download/records.jsonl",
    ],
)
def test_auth(setup, monkeypatch, path):
    c, _ = setup
    assert c.get(path).status_code == 401
    assert c.get(path, headers={"Authorization": "Bearer bad"}).status_code == 401
    monkeypatch.delenv("WORKER_TOKEN")
    assert c.get(path).status_code == 503


def test_flag_and_invalid_data(setup, monkeypatch):
    c, root = setup
    monkeypatch.setenv("ATK_PREPARATION_ENABLED", "false")
    assert submit(c).status_code == 503
    monkeypatch.setenv("ATK_PREPARATION_ENABLED", "true")
    (root / "inputs/bad.txt").write_bytes(b"\xff")
    assert submit(c, source_path="bad.txt").status_code == 400
    assert submit(c, source_path="../other.txt").status_code == 400
    (root / "inputs/link.txt").symlink_to(root / "inputs/Synthetic ü.txt")
    assert submit(c, source_path="link.txt").status_code == 400
    (root / "external_configs/private.yaml").write_text(
        "preparation: {chunk_chars: true}"
    )
    assert submit(c).status_code == 400


def test_spawn_failure_no_false_success(setup):
    c, _root = setup
    with patch("pilot_prepare.subprocess.Popen", side_effect=OSError("secret")):
        r = submit(c)
    assert r.status_code == 503 and "secret" not in r.text
    assert c.get("/pilot/preparation", headers=AUTH).json() == []


def test_interrupted_restart_fails_closed(setup):
    c, _root = setup
    service = pilot.service()
    import uuid

    job = str(uuid.uuid4())
    directory = service.root / job
    directory.mkdir(mode=0o700)
    pilot.write_json(directory / "state.json", {"job_id": job, "status": "RUNNING"})
    (directory / "source.txt").write_text("Synthetic")
    recovered = pilot.Preparation()
    assert recovered.status(job)["status"] == "FAILED"
    assert not (directory / "source.txt").exists()
    assert (
        c.get(
            f"/pilot/preparation/{job}/download/records.jsonl", headers=AUTH
        ).status_code
        == 409
    )


def test_chunk_explosion_bounded():
    with pytest.raises(ValueError):
        pilot.records(
            b"x" * pilot.MAX_INPUT,
            b"preparation: {chunk_chars: 64, overlap_chars: 63}",
            "synthetic.txt",
        )


def test_one_active_timeout_and_no_partial_export(setup):
    import subprocess
    from unittest.mock import MagicMock

    c, root = setup
    process = MagicMock()
    process.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 15), 0]
    process.returncode = -9
    with (
        patch("pilot_prepare.subprocess.Popen", return_value=process),
        patch("pilot_prepare.threading.Thread"),
    ):
        r = submit(c)
        job = r.json()["job_id"]
        assert submit(c).status_code == 409
        assert (
            c.get(
                f"/pilot/preparation/{job}/download/records.jsonl", headers=AUTH
            ).status_code
            == 409
        )
        pilot.service().watch(job, process)
    process.kill.assert_called_once()
    assert wait(c, job)["status"] == "FAILED"
    assert not (root / "pilot_state" / job / "source.txt").exists()
    assert not (root / "outputs/preparation" / job).exists()


def test_artifact_tampering_denies_export(setup):
    c, root = setup
    job = submit(c).json()["job_id"]
    assert wait(c, job)["status"] == "COMPLETED"
    (root / "outputs/preparation" / job / "records.jsonl").write_text("tampered")
    assert (
        c.get(
            f"/pilot/preparation/{job}/download/records.jsonl", headers=AUTH
        ).status_code
        == 409
    )


def test_config_and_source_snapshots_are_immutable(setup):
    c, root = setup
    with (
        patch("pilot_prepare.threading.Thread"),
        patch("pilot_prepare.subprocess.Popen"),
    ):
        job = submit(c).json()["job_id"]
    directory = root / "pilot_state" / job
    raw = (directory / "source.txt").read_bytes()
    (root / "inputs/Synthetic ü.txt").write_text("changed after submit")
    (root / "external_configs/private.yaml").write_text("changed: after-submit")
    pilot.execute(directory, root / "outputs/preparation" / job)
    state = wait(c, job)
    assert state["source_sha256"] == pilot.digest(raw)
    assert state["status"] == "COMPLETED"
    pilot.service().active = None


@pytest.mark.parametrize(
    "raw",
    [b"x: &x [*x]", b"x: [" + b"[" * 20 + b"0" + b"]" * 21, b"x: " + b"a" * 17000],
)
def test_preparation_yaml_complexity_is_bounded(raw):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        pilot.parameters(raw)


def test_corrupt_state_fails_closed(setup):
    c, root = setup
    job = submit(c).json()["job_id"]
    assert wait(c, job)["status"] == "COMPLETED"
    (root / "pilot_state" / job / "state.json").write_text("corrupt synthetic state")
    r = c.get(f"/pilot/preparation/{job}", headers=AUTH)
    assert r.status_code == 503 and "synthetic" not in r.text
