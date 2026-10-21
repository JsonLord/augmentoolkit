"""Synthetic, isolated control-plane tests. Never execute a pipeline or call HF."""

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from huey.exceptions import HueyException
from PIL import Image
from redis.exceptions import ConnectionError, TimeoutError

import api
import queue_readiness
import tasks
from app_storage import StorageViolation, confined_path, normalize_mutable_paths
from file_operation_helpers import get_dir_structure, get_safe_path, zip_directory
from ocr.adapters.cpu_http import RemoteCPU
from ocr.config import Settings
from ocr.schemas import BackendHealth, OCROptions, OCRPageResult, RasterPage, SafeError
from ocr.worker_api import create_app

AUTH = {"Authorization": "Bearer synthetic-operator-token"}


@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    monkeypatch.setenv("WORKER_TOKEN", "synthetic-operator-token")
    monkeypatch.delenv("ATK_JOB_EXECUTION_ENABLED", raising=False)
    monkeypatch.delenv("HF_WORK_REPO", raising=False)
    monkeypatch.delenv("ATK_HF_RECOVERY_ENABLED", raising=False)
    monkeypatch.setattr(
        api, "run_pipeline_task", MagicMock(side_effect=AssertionError("No real jobs"))
    )
    monkeypatch.setattr(
        api, "HFHubManager", MagicMock(side_effect=AssertionError("No HF traffic"))
    )


# Collect from the actual protected router so new routes cannot escape the matrix.
ROUTES = [(next(iter(r.methods)), r.path) for r in api.router.routes]


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("configured", [False, True])
def test_every_parent_route_fail_closed(monkeypatch, method, path, configured):
    if not configured:
        monkeypatch.delenv("WORKER_TOKEN")
    path = (
        path.replace("{task_id}", "synthetic")
        .replace("{job_id}", "synthetic")
        .replace("{relative_path:path}", "synthetic")
    )
    response = TestClient(api.app).request(
        method, path, json={"node_path": "synthetic"}
    )
    assert response.status_code == (401 if configured else 503), (
        method,
        path,
        response.text,
    )
    assert "synthetic-operator-token" not in response.text


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("private redis uri"),
        TimeoutError("111 private"),
        HueyException("private huey"),
    ],
)
@pytest.mark.parametrize(
    "path,method",
    [
        ("/tasks/queue", "GET"),
        ("/tasks/t/status", "GET"),
        ("/tasks/t/parameters", "GET"),
        ("/tasks/t/interrupt", "POST"),
        ("/tasks/t/outputs/download", "GET"),
        ("/pipelines/run", "POST"),
        ("/jobs/j/resume", "POST"),
        ("/jobs/j/fork", "POST"),
    ],
)
def test_queue_unavailable_is_typed_503(monkeypatch, error, path, method):
    monkeypatch.setattr(
        queue_readiness,
        "redis_client",
        SimpleNamespace(ping=MagicMock(side_effect=error)),
    )
    response = TestClient(api.app).request(
        method, path, headers=AUTH, json={"node_path": "synthetic"}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Huey/Redis unavailable"
    api.run_pipeline_task.assert_not_called()


def test_queue_is_not_execution_permission(monkeypatch):
    monkeypatch.setattr(
        queue_readiness, "redis_client", SimpleNamespace(ping=lambda: True)
    )
    monkeypatch.setattr(
        queue_readiness, "huey", SimpleNamespace(pending_count=lambda: 0)
    )
    response = TestClient(api.app).post(
        "/pipelines/run", headers=AUTH, json={"node_path": "synthetic"}
    )
    assert response.status_code == 503 and "disabled" in response.text
    api.run_pipeline_task.assert_not_called()


def test_outage_after_readiness_is_503(monkeypatch):
    monkeypatch.setattr(api, "require_job_execution", lambda: None)
    api.run_pipeline_task.side_effect = ConnectionError("private uri")
    r = TestClient(api.app).post(
        "/pipelines/run", headers=AUTH, json={"node_path": "synthetic"}
    )
    assert r.status_code == 503 and "private" not in r.text


def test_message_111_does_not_classify_non_queue_failure(monkeypatch):
    monkeypatch.setattr(api, "require_job_execution", lambda: None)
    api.run_pipeline_task.side_effect = ValueError("111 secret")
    r = TestClient(api.app).post(
        "/pipelines/run", headers=AUTH, json={"node_path": "synthetic"}
    )
    assert r.status_code == 500 and "secret" not in r.text


@pytest.mark.parametrize(
    "value", ["../outputs-evil/x", "/etc/passwd", "nested/../../outside"]
)
def test_output_path_escape_rejected_before_queue(tmp_path, monkeypatch, value):
    monkeypatch.setenv("ATK_DATA_ROOT", str(tmp_path / "data"))
    with pytest.raises(StorageViolation):
        tasks.resolve_job_output_dir("unused", parameters={"output_dir": value})
    with pytest.raises(StorageViolation):
        normalize_mutable_paths({"nested": {"chunking_output_dir": value}})
    r = TestClient(api.app).post(
        "/pipelines/run",
        headers=AUTH,
        json={"node_path": "synthetic", "parameters": {"output_dir": value}},
    )
    assert r.status_code == 400
    api.run_pipeline_task.assert_not_called()


@pytest.mark.parametrize("target", ["outside", "inside", "dangling"])
def test_symlinks_denied_every_file_boundary(tmp_path, target):
    base = tmp_path / "data"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = {"inside": base, "outside": outside, "dangling": outside / "missing"}[
        target
    ]
    (base / "link").symlink_to(destination, target_is_directory=True)
    with pytest.raises(StorageViolation):
        confined_path(base, "link/private.txt")
    with pytest.raises(HTTPException):
        get_safe_path(base, "link")
    with pytest.raises(HTTPException):
        get_dir_structure(base)
    with pytest.raises(HTTPException):
        zip_directory(base, tmp_path / "archive.zip")


def test_valid_output_and_sibling_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("ATK_DATA_ROOT", str(tmp_path / "private"))
    result = Path(tasks.resolve_job_output_dir("unused", durable_job_id="job"))
    assert result == tmp_path / "private/outputs/job"
    assert result.is_dir()
    assert tasks.resolve_job_output_dir(
        "unused", parameters={"output_dir": str(result)}
    ) == str(result)
    with pytest.raises(HTTPException):
        get_safe_path(result, "../job-evil")


def test_generation_config_uses_private_storage(tmp_path, monkeypatch):
    from generation.core_components.write_config_files import write_training_config

    monkeypatch.setenv("ATK_DATA_ROOT", str(tmp_path / "private"))
    output = Path(write_training_config([], "synthetic-model"))
    assert output == tmp_path / "private/generation/training_config.yaml"
    assert output.is_file()
    with pytest.raises(StorageViolation):
        write_training_config([], "synthetic-model", output_path="../escape")


class FakeCPU:
    calls = 0

    async def initialize(self):
        pass

    async def health(self):
        return BackendHealth(ready=True)

    async def capabilities(self):
        return {}

    async def recognize(self, page, options):
        self.calls += 1
        assert options.sensitivity == "D0"
        return OCRPageResult(
            page_index=0,
            source="cpu_ocr",
            engine="pp-ocrv6-small",
            text="Synthetic D0",
            markdown="Synthetic D0",
            image_width=page.width,
            image_height=page.height,
        )


def test_combined_lifespan_enters_and_exits_child(monkeypatch):
    from contextlib import asynccontextmanager

    calls = []

    @asynccontextmanager
    async def child(app):
        calls.append("start")
        app.state.backend = FakeCPU()
        yield
        calls.append("stop")

    monkeypatch.setitem(
        api.ocr_app.user_middleware[0].kwargs, "token", "synthetic-operator-token"
    )
    monkeypatch.setattr(api.ocr_app, "middleware_stack", None)
    monkeypatch.setattr(api.ocr_app.router, "lifespan_context", child)
    with TestClient(api.app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ocr/health").status_code == 200
        assert client.get("/ocr/ready").status_code == 401
        assert client.get("/ocr/ready", headers=AUTH).json() == {"ready": True}
        assert client.get("/ocr/docs").status_code == 200
    assert calls == ["start", "stop"]


@pytest.mark.parametrize("classification", [None, "D1", "D2"])
@pytest.mark.parametrize(
    "endpoint", ["/internal/ocr/page", "/v1/ocr/page", "/v1/chat/completions"]
)
def test_worker_rejects_unclassified_sensitive(monkeypatch, classification, endpoint):
    backend = FakeCPU()
    child = create_app(Settings(worker_token="synthetic-operator-token"), backend)
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="PNG")
    image = buffer.getvalue()
    with TestClient(child) as client:
        if endpoint.endswith("completions"):
            body = {
                "model": "ocr-cpu",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,"
                                    + base64.b64encode(image).decode()
                                },
                            }
                        ],
                    }
                ],
            }
            if classification:
                body["ocr_options"] = {"sensitivity": classification}
            response = client.post(endpoint, json=body, headers=AUTH)
        else:
            response = client.post(
                endpoint,
                headers=AUTH,
                files={"file": ("d0.png", image)},
                data={} if classification is None else {"sensitivity": classification},
            )
        assert response.status_code == 422
        assert backend.calls == 0


@pytest.mark.asyncio
async def test_dispatcher_worker_d0_protocol_and_classification():
    backend = FakeCPU()
    child = create_app(Settings(worker_token="synthetic-operator-token"), backend)
    remote = RemoteCPU(
        "http://test",
        "synthetic-operator-token",
        transport=httpx.ASGITransport(app=child),
    )
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="PNG")
    page = RasterPage(0, buffer.getvalue(), 100, 100, "synthetic")
    try:
        async with child.router.lifespan_context(child):
            options = OCROptions(sensitivity="D0")
            result = await remote.recognize(page, options)
            assert result.text == "Synthetic D0" and options.sensitivity == "D0"
            assert result.provider_destination == "http://test"
            for classification in ["D1", "D2"]:
                options = OCROptions(sensitivity=classification)
                with pytest.raises(SafeError, match="explicit_d0_required"):
                    await remote.recognize(page, options)
                assert options.sensitivity == classification
        assert backend.calls == 1
    finally:
        await remote.close()


@pytest.mark.parametrize(
    "value", ["../outputs-evil/x", "/etc/passwd", "nested/../../outside"]
)
def test_config_paths_cannot_read_arbitrary_files(tmp_path, monkeypatch, value):
    monkeypatch.setenv("ATK_DATA_ROOT", str(tmp_path / "private"))
    with pytest.raises(StorageViolation):
        tasks.resolve_job_output_dir("unused", config_path=value)


def test_chunk_metadata_cannot_escape_output(tmp_path, monkeypatch):
    from generation.core_components.chunking import write_text

    monkeypatch.setenv("ATK_DATA_ROOT", str(tmp_path / "private"))
    assert write_text("job", [{"text": "synthetic", "metadata": "a.txt"}]) == 1
    assert (tmp_path / "private/outputs/job/a.txt").read_text() == "synthetic"
    for value in ["../job-evil/leak.txt", "/tmp/leak.txt"]:
        with pytest.raises(StorageViolation):
            write_text("job", [{"text": "synthetic", "metadata": value}])


def test_every_business_route_is_protected():
    from fastapi.routing import APIRoute

    from app_auth import verify_token

    public = {"/health", "/api-docs", "/{full_path:path}"}
    for route in api.app.routes:
        if isinstance(route, APIRoute) and route.path not in public:
            assert any(
                dep.call is verify_token for dep in route.dependant.dependencies
            ), route.path


@pytest.mark.asyncio
@pytest.mark.parametrize("classification", ["D0", "D1", "D2"])
async def test_cpu_retains_verified_source_classification(tmp_path, classification):
    from ocr.adapters.rapidocr_cpu import RapidCPU

    cpu = RapidCPU(tmp_path)
    cpu.engine = object()
    page = RasterPage(0, b"synthetic", 100, 100, "source-page")
    cpu._recognize = lambda page: OCRPageResult(
        page_index=0,
        source="cpu_ocr",
        engine="pp-ocrv6-small",
        text="synthetic",
        markdown="synthetic",
        image_width=100,
        image_height=100,
    )
    options = OCROptions(sensitivity=classification)
    result = await cpu.recognize(page, options)
    assert result.sensitivity == options.sensitivity == classification
    assert result.page_id == page.page_id


def test_worker_invalid_storage_does_not_acquire_lock(monkeypatch):
    monkeypatch.setenv("ATK_JOB_EXECUTION_ENABLED", "true")
    redis = MagicMock()
    monkeypatch.setattr(tasks, "redis_client", redis)
    with pytest.raises(StorageViolation):
        tasks.run_pipeline_task.call_local(
            task=SimpleNamespace(id="synthetic"),
            node_path="unused",
            parameters={"output_dir": "/etc/outside"},
        )
    redis.set.assert_not_called()
