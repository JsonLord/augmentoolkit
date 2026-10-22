"""Document routing regressions; no model, queue, provider or job execution."""

import pytest
from fastapi.testclient import TestClient

import api

PAGES = [
    "/",
    "/inputs",
    "/outputs",
    "/configs",
    "/monitor",
    "/logs",
    "/chat",
    "/configs/example",
    "/logs/example",
]


@pytest.fixture
def frontend(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<html><div id="root"></div></html>')
    (tmp_path / "assets/app.js").write_text('console.log("synthetic");')
    monkeypatch.setattr(api, "FRONTEND_DIST_DIR", tmp_path)
    monkeypatch.setenv("WORKER_TOKEN", "synthetic-token")
    return TestClient(api.app)


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("suffix", ["", "/"])
def test_react_direct_navigation_and_refresh(frontend, path, suffix):
    path = path.rstrip("/") + suffix if path != "/" else path
    for _ in range(2):
        r = frontend.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert 'id="root"' in r.text


@pytest.mark.parametrize(
    "path",
    [
        "/pipelines/missing",
        "/tasks/missing",
        "/jobs/missing/unknown",
        "/inputs/missing",
        "/configs/content",
        "/configs/structure",
        "/logs/structure",
        "/llm/missing",
        "/api/missing",
        "/configs/example/extra",
        "/unknown",
        "/assets/missing.js",
        "/assets/missing.css",
        "/missing.js",
        "/index.html",
    ],
)
def test_unknown_api_and_missing_assets_are_json_404(frontend, path):
    r = frontend.get(path, headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    "path",
    [
        "/inputs/structure/",
        "/configs/structure/",
        "/outputs/structure/",
        "/tasks/queue",
        "/jobs",
    ],
)
def test_page_routing_does_not_bypass_data_auth(frontend, path):
    r = frontend.get(path, headers={"Accept": "text/html"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")


def test_static_asset_and_ocr_mount_preserved(frontend):
    r = frontend.get("/assets/app.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert frontend.get("/ocr/health").json() == {"status": "ok"}
    assert frontend.get("/ocr/ready").status_code in {401, 503}


@pytest.mark.asyncio
async def test_overlapping_ocr_is_bounded_before_second_inference():
    """A held synthetic backend verifies admission, without repeated real OCR."""
    import asyncio
    import io

    import httpx
    from PIL import Image

    from ocr.config import Settings
    from ocr.schemas import BackendHealth, OCRPageResult
    from ocr.worker_api import create_app

    entered, release = asyncio.Event(), asyncio.Event()

    class HeldCPU:
        calls = 0

        async def health(self):
            return BackendHealth(ready=True)

        async def recognize(self, page, options):
            self.calls += 1
            entered.set()
            await release.wait()
            return OCRPageResult(
                page_index=0,
                source="cpu_ocr",
                text="synthetic",
                markdown="synthetic",
                image_width=page.width,
                image_height=page.height,
                sensitivity=options.sensitivity,
            )

    backend = HeldCPU()
    child = create_app(Settings(worker_token="synthetic"), backend)
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="PNG")
    async with (
        child.router.lifespan_context(child),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=child), base_url="http://test"
        ) as client,
    ):

        async def request():
            return await client.post(
                "/v1/ocr/page",
                headers={"Authorization": "Bearer synthetic"},
                data={"sensitivity": "D0"},
                files={"file": ("synthetic.png", buffer.getvalue())},
            )

        first = asyncio.create_task(request())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            second = await request()
            assert second.status_code == 503
            assert second.json() == {"code": "worker_busy"}
            assert backend.calls == 1
        finally:
            release.set()
            await first
        assert first.result().status_code == 200
