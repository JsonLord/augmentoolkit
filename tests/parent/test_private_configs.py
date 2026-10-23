"""Synthetic private YAML lifecycle and fail-closed editing boundaries."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api
import private_configs
import tasks

AUTH = {"Authorization": "Bearer synthetic"}


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "data"
    base = root / "external_configs"
    base.mkdir(parents=True, mode=0o700)
    source = tmp_path / "source"
    source.mkdir()
    template = source / "template.yaml"
    template.write_bytes(b"title: Synthetic\nvalue: original\n")
    template.chmod(0o444)
    aliases = source / "super_config.yaml"
    aliases.write_text(
        "path_aliases:\n  synthetic-config: template.yaml\npipeline_order: []\n"
    )
    monkeypatch.setenv("WORKER_TOKEN", "synthetic")
    monkeypatch.setenv("ATK_DATA_ROOT", str(root))
    monkeypatch.setattr(api, "CONFIGS_DIR", base)
    monkeypatch.setattr(api, "SUPER_CONFIG_PATH", aliases)
    monkeypatch.setattr(tasks, "SUPER_CONFIG_PATH", aliases)
    monkeypatch.setattr(tasks, "ATK3_DIRECTORY", str(source))
    return TestClient(api.app), base, template


def save(c, name, data=b"value: new\n", version=None):
    return c.post(
        "/configs/content/" + name,
        content=data,
        headers={**AUTH, **({"If-Match": version} if version else {})},
    )


def duplicate(c, name="private ü.yaml", source="synthetic-config"):
    return c.post(
        "/configs/duplicate",
        headers=AUTH,
        json={"source_alias": source, "destination_relative_path": name},
    )


def test_lifecycle(storage):
    c, base, template = storage
    r = c.get("/configs/content/synthetic-config", headers=AUTH)
    assert r.status_code == 200 and r.content == template.read_bytes()
    assert r.headers["x-config-read-only"] == "true"
    assert save(c, "synthetic-config").status_code == 403
    assert duplicate(c).status_code == 200
    assert duplicate(c).status_code == 409
    r = c.get("/configs/content/private ü.yaml", headers=AUTH)
    assert r.headers["x-config-read-only"] == "false"
    original = r.headers["etag"]
    data = "title: Synthetic ü\nvalue: updated\n".encode()
    r = save(c, "private ü.yaml", data, original)
    assert r.status_code == 200
    assert (base / "private ü.yaml").read_bytes() == data
    assert (base / "private ü.yaml").stat().st_mode & 0o777 == 0o600
    assert save(c, "private ü.yaml", version=original).status_code == 409
    assert save(c, "private ü.yaml").status_code == 409
    assert template.read_bytes() == b"title: Synthetic\nvalue: original\n"
    assert not list((base.parent / "file_staging").iterdir())


@pytest.mark.parametrize(
    "data",
    [
        b"value: [",
        b"!!python/object/apply:os.system ['echo unsafe']",
        b"- list",
        b"\xff",
        b"",
        b"scalar",
    ],
)
def test_invalid_yaml_never_published(storage, data):
    c, base, _ = storage
    r = save(c, "bad.yaml", data)
    assert r.status_code == 400 and not (base / "bad.yaml").exists()
    assert "unsafe" not in r.text


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/configs/content/synthetic-config"),
        ("POST", "/configs/content/a.yaml"),
        ("POST", "/configs/duplicate"),
        ("POST", "/configs/directory"),
        ("POST", "/configs/move"),
        ("DELETE", "/configs/a.yaml"),
    ],
)
@pytest.mark.parametrize("mode", ["missing", "invalid", "unconfigured"])
def test_auth(storage, monkeypatch, method, path, mode):
    c, _, _ = storage
    if mode == "unconfigured":
        monkeypatch.delenv("WORKER_TOKEN")
    r = c.request(
        method,
        path,
        headers={"Authorization": "Bearer wrong"} if mode == "invalid" else {},
        json={},
    )
    assert r.status_code == (503 if mode == "unconfigured" else 401)


def test_concurrent_stale_writers(storage):
    c, base, _ = storage
    assert save(c, "a.yaml").status_code == 200
    version = c.get("/configs/content/a.yaml", headers=AUTH).headers["etag"]
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda n: (
                    save(c, "a.yaml", f"value: {n}\n".encode(), version).status_code
                ),
                [1, 2],
            )
        )
    assert sorted(results) == [200, 409]
    assert not list((base.parent / "file_staging").iterdir())


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_write_error_preserves_original_and_cleans_stage(storage, operation):
    c, base, _ = storage
    assert save(c, "a.yaml").status_code == 200
    data = (base / "a.yaml").read_bytes()
    version = private_configs.etag(data)
    with patch("private_configs.os." + operation, side_effect=OSError("private-error")):
        r = save(c, "a.yaml", b"value: changed\n", version)
    assert r.status_code == 500 and "private-error" not in r.text
    assert (base / "a.yaml").read_bytes() == data
    assert not list((base.parent / "file_staging").iterdir())


def test_links_limits_and_missing(storage):
    c, base, template = storage
    (base / "link.yaml").symlink_to(template)
    assert c.get("/configs/content/link.yaml", headers=AUTH).status_code == 400
    assert save(c, "link.yaml").status_code == 400
    assert save(c, "%2e%2e/outside.yaml").status_code in (400, 404)
    assert save(c, "a.txt").status_code == 400
    assert save(c, "missing/a.yaml").status_code == 404
    assert c.get("/configs/content/absent.yaml", headers=AUTH).status_code == 404
    assert duplicate(c, source=str(template)).status_code == 400
    assert (
        save(c, "big.yaml", b"v: " + b"x" * private_configs.MAX_BYTES).status_code
        == 413
    )
    assert not (base / "big.yaml").exists()


def test_deleted_version_and_directory_operations(storage):
    c, base, _ = storage
    assert (
        c.post(
            "/configs/directory", headers=AUTH, json={"relative_path": "nested ü"}
        ).status_code
        == 200
    )
    assert duplicate(c, "nested ü/a.yaml").status_code == 200
    r = c.get("/configs/content/nested ü/a.yaml", headers=AUTH)
    assert c.delete("/configs/nested ü/a.yaml", headers=AUTH).status_code == 200
    assert save(c, "nested ü/a.yaml", version=r.headers["etag"]).status_code == 409
    assert not (base / "nested ü/a.yaml").exists()
    assert c.delete("/configs/", headers=AUTH).status_code == 400


def test_concurrent_duplicate_creation(storage):
    c, base, template = storage
    with ThreadPoolExecutor(2) as pool:
        codes = list(
            pool.map(lambda _: duplicate(c, "same.yaml").status_code, range(2))
        )
    assert sorted(codes) == [200, 409]
    assert (base / "same.yaml").read_bytes() == template.read_bytes()


def test_invalid_save_preserves_version(storage):
    c, base, _ = storage
    assert save(c, "a.yaml").status_code == 200
    before = c.get("/configs/content/a.yaml", headers=AUTH)
    assert (
        save(c, "a.yaml", b"secret-value: [", before.headers["etag"]).status_code == 400
    )
    after = c.get("/configs/content/a.yaml", headers=AUTH)
    assert (
        before.content == after.content
        and before.headers["etag"] == after.headers["etag"]
    )
    assert not list((base.parent / "file_staging").iterdir())
