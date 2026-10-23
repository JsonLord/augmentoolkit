"""Synthetic file lifecycle, atomic publication, auth and filesystem boundaries."""

import io
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api
import input_files

AUTH = {"Authorization": "Bearer synthetic"}


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    inputs = root / "inputs"
    inputs.mkdir(mode=0o700)
    monkeypatch.setenv("ATK_DATA_ROOT", str(root))
    monkeypatch.setenv("WORKER_TOKEN", "synthetic")
    monkeypatch.setattr(api, "INPUTS_DIR", inputs)
    return TestClient(api.app), inputs


def upload(client, name="ü space.txt", data=b"Synthetic D0", target=""):
    return client.post(
        "/inputs/upload/" + target, headers=AUTH, files={"files": (name, data)}
    )


def test_full_lifecycle_and_private_modes(storage):
    c, base = storage
    assert upload(c).status_code == 200
    assert (base / "ü space.txt").stat().st_mode & 0o777 == 0o600
    assert c.get("/inputs/structure/", headers=AUTH).json()[0]["path"] == "ü space.txt"
    r = c.get("/inputs/download/ü space.txt", headers=AUTH)
    assert (
        r.content == b"Synthetic D0"
        and "attachment" in r.headers["content-disposition"]
    )
    assert not list((base.parent / "file_staging").iterdir())
    assert (
        c.post(
            "/inputs/directory", headers=AUTH, json={"relative_path": "nested ü"}
        ).status_code
        == 200
    )
    assert (base / "nested ü").stat().st_mode & 0o777 == 0o700
    r = c.post(
        "/inputs/move",
        headers=AUTH,
        json={
            "source_relative_path": "ü space.txt",
            "destination_relative_path": "nested ü/renamed.txt",
        },
    )
    assert r.status_code == 200
    assert upload(c, "another.txt", target="nested ü").status_code == 200
    r = c.get("/inputs/download/nested ü", headers=AUTH)
    with ZipFile(io.BytesIO(r.content)) as z:
        assert z.read("renamed.txt") == b"Synthetic D0"
    assert c.delete("/inputs/nested ü/renamed.txt", headers=AUTH).status_code == 200
    assert (
        c.get("/inputs/download/nested ü/renamed.txt", headers=AUTH).status_code == 404
    )
    assert c.delete("/inputs/nested ü", headers=AUTH).status_code == 200
    assert c.get("/inputs/structure/", headers=AUTH).json() == []


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/inputs/upload/"),
        ("GET", "/inputs/download/a"),
        ("DELETE", "/inputs/a"),
        ("POST", "/inputs/directory"),
        ("POST", "/inputs/move"),
    ],
)
@pytest.mark.parametrize("mode", ["missing", "invalid", "unconfigured"])
def test_auth(storage, monkeypatch, method, path, mode):
    c, _ = storage
    if mode == "unconfigured":
        monkeypatch.delenv("WORKER_TOKEN")
    r = c.request(
        method,
        path,
        headers={} if mode != "invalid" else {"Authorization": "Bearer wrong"},
        json={},
    )
    assert r.status_code == (503 if mode == "unconfigured" else 401)


@pytest.mark.parametrize(
    "value",
    ["../inputs-evil/file", "/etc/passwd", "a/../x", "bad\\name", "bad\x00name"],
)
def test_paths(storage, value):
    _, base = storage
    with pytest.raises(HTTPException) as caught:
        input_files.path(base, value)
    assert caught.value.status_code == 400


@pytest.mark.parametrize("operation", ["download", "delete", "tree", "move"])
@pytest.mark.parametrize("destination", ["inside", "outside", "dangling"])
def test_symlink_and_descendant_links_denied(storage, operation, destination, tmp_path):
    _, base = storage
    (base / "folder").mkdir(mode=0o700)
    targets = {"inside": base, "outside": tmp_path, "dangling": tmp_path / "absent"}
    (base / "folder/link").symlink_to(targets[destination])
    f = getattr(input_files, operation)
    args = [base, "folder"] + (["new"] if operation == "move" else [])
    with pytest.raises(HTTPException) as caught:
        f(*args)
    assert caught.value.status_code == 400
    assert (base / "folder/link").is_symlink()


def test_conflicts_and_no_root_delete(storage):
    c, base = storage
    assert upload(c).status_code == 200
    assert upload(c, data=b"replacement").status_code == 409
    assert (base / "ü space.txt").read_bytes() == b"Synthetic D0"
    assert c.delete("/inputs/.", headers=AUTH).status_code == 400
    assert (
        c.post(
            "/inputs/directory", headers=AUTH, json={"relative_path": "."}
        ).status_code
        == 400
    )
    c.post("/inputs/directory", headers=AUTH, json={"relative_path": "folder"})
    assert (
        c.post(
            "/inputs/directory", headers=AUTH, json={"relative_path": "folder"}
        ).status_code
        == 409
    )
    assert (
        c.post(
            "/inputs/move",
            headers=AUTH,
            json={
                "source_relative_path": "ü space.txt",
                "destination_relative_path": "folder",
            },
        ).status_code
        == 409
    )


def test_size_failure_cleanup_and_disk_error(storage, monkeypatch):
    c, base = storage
    monkeypatch.setattr(input_files, "MAX_UPLOAD_BYTES", 4)
    assert upload(c, data=b"12345").status_code == 413
    assert not list(base.iterdir()) and not list(
        (base.parent / "file_staging").iterdir()
    )
    with patch.object(
        input_files.os, "fsync", side_effect=OSError("private disk details")
    ):
        r = upload(c, data=b"123")
    assert r.status_code == 500 and "private" not in r.text
    assert not list(base.iterdir()) and not list(
        (base.parent / "file_staging").iterdir()
    )
    r = c.post(
        "/inputs/upload/",
        headers={**AUTH, "Content-Length": str(1000000)},
        content=b"ignored",
    )
    assert r.status_code == 413


def test_concurrent_duplicate_publish(storage):
    c, base = storage
    with ThreadPoolExecutor(2) as pool:
        statuses = list(pool.map(lambda _: upload(c).status_code, range(2)))
    assert sorted(statuses) == [200, 409]
    assert (base / "ü space.txt").read_bytes() == b"Synthetic D0"


def test_batch_rollback_on_publish_error(storage):
    c, base = storage
    original = os.link
    calls = 0

    def fail_second(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic disk error")
        original(src, dst)

    with patch.object(input_files.os, "link", side_effect=fail_second):
        r = c.post(
            "/inputs/upload/",
            headers=AUTH,
            files=[("files", ("a.txt", b"a")), ("files", ("b.txt", b"b"))],
        )
    assert r.status_code == 500
    assert not list(base.iterdir()) and not list(
        (base.parent / "file_staging").iterdir()
    )


def test_chunked_request_body_is_bounded_before_multipart(storage, monkeypatch):
    c, base = storage
    monkeypatch.setattr(input_files, "MAX_UPLOAD_BYTES", 4)
    r = c.post(
        "/inputs/upload/", headers=AUTH, content=iter([b"x" * 40000, b"x" * 40000])
    )
    assert r.status_code == 413
    assert not list(base.iterdir())
