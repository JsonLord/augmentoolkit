import io

import httpx
import pytest
from conftest import Fake
from ocr.adapters.cpu_http import RemoteCPU
from ocr.api import create_app
from ocr.config import Settings
from ocr.handoff import anonymization_handoff
from ocr.routing import Dispatcher
from ocr.schemas import OCROptions, OCRResult, RasterPage, SafeError
from PIL import Image

H = {"Authorization": "Bearer test"}


async def test_partial_order_and_logs(png, caplog):
    class FailOne(Fake):
        async def recognize(self, page, options):
            if page.index == 1:
                raise RuntimeError("PRIVATE_SECRET_IMAGE_TEXT")
            return await super().recognize(page, options)

    b = io.BytesIO()
    im = Image.new("RGB", (100, 100), "white")
    im.save(b, format="TIFF", save_all=True, append_images=[im, im])
    app = create_app(Settings(token="test"), FailOne())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        response = await c.post(
            "/v1/ocr/jobs", headers=H, files={"file": ("test.tiff", b.getvalue())}
        )
        id = response.json()["job_id"]
        job = app.state.jobs.jobs[id]
        await job.task
        r = await c.get(f"/v1/ocr/jobs/{id}/result", headers=H)
        body = r.json()
        assert body["status"] == "partial"
        assert [p["page_index"] for p in body["pages"]] == [0, 2]
        assert body["failures"] == [{"page_index": 1, "code": "backend_failure"}]
        assert "PRIVATE_SECRET_IMAGE_TEXT" not in r.text + caplog.text
        metrics = (await c.get("/metrics", headers=H)).text
        assert "PRIVATE_SECRET" not in metrics and "Public fixture" not in metrics


async def test_upload_chunked_rate_options(png):
    app = create_app(Settings(token="test", max_upload=50, rate_per_minute=3), Fake())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):

        async def chunks():
            yield b"x" * 40000
            yield b"x" * 40000

        assert (await c.post("/v1/ocr/jobs", headers=H, content=chunks())).status_code == 413
        r = await c.post("/v1/ocr/page", headers=H, files={"file": ("a.png", png)})
        assert r.status_code == 413
        assert (
            await c.post("/v1/chat/completions", headers=H, json={"private": "never echoed"})
        ).status_code == 422
        assert (await c.post("/v1/chat/completions", headers=H, json={})).status_code == 429


async def test_no_token_and_remote_unavailable(png):
    app = create_app(Settings(), Fake())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        assert (await c.get("/ready")).status_code == 503
    remote = RemoteCPU(
        "https://worker.invalid",
        "fake",
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
    )
    try:
        assert not (await remote.health()).ready
    finally:
        await remote.close()


async def test_quality_evidence_and_handoff(png):
    d = Dispatcher(
        Fake(confidence=0.1),
        Fake("gpu", text="Other public transcription"),
        Settings(gpu_enabled=True),
    )
    d.start()
    try:
        p = await d.page(RasterPage(0, png, 200, 120, "id"), OCROptions(sensitivity="D0"))
        assert p.alternatives and "needs_review" in p.flags and "disagreement" in p.flags
        assert p.attempt_count == 2 and p.alternatives[0].sensitivity == "D0"
        r = OCRResult(
            job_id="j",
            request_id="r",
            status="completed",
            page_count=1,
            sensitivity="D0",
            markdown=p.markdown,
            text=p.text,
            pages=[p],
            failures=[],
        )
        h = anonymization_handoff(r, lambda text: {"text": text, "tags": []})
        assert h["anonymous"] is False and h["pages"][0]["page_id"] == "id" and p.text == r.text
        with pytest.raises(SafeError):
            anonymization_handoff(r, lambda text: "silently declassified")
    finally:
        await d.close()


async def test_timeout_and_result_capacity(png):
    d = Dispatcher(Fake(delay=0.1), None, Settings(timeout=0.01))
    d.start()
    try:
        with pytest.raises(SafeError, match="backend_timeout"):
            await d.page(RasterPage(0, png, 200, 120, "id"), OCROptions())
        assert len(d.cpu.backend.calls) == 2
    finally:
        await d.close()
    app = create_app(Settings(token="test", max_result_bytes=20), Fake())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        r = await c.post("/v1/ocr/jobs", headers=H, files={"file": ("a.png", png)})
        j = app.state.jobs.jobs[r.json()["job_id"]]
        await j.task
        assert j.status == "failed" and j.result is None


async def test_missing_models_readiness_and_output(png, tmp_path):
    from ocr.adapters.rapidocr_cpu import RapidCPU

    cpu = RapidCPU(tmp_path)
    with pytest.raises(SafeError, match="model_integrity_unavailable"):
        await cpu.initialize()
    assert not (await cpu.health()).ready
    app = create_app(Settings(token="test"), cpu)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        assert (await c.get("/ready", headers=H)).status_code == 503
    app = create_app(Settings(token="test"), Fake())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        r = await c.post(
            "/v1/ocr/jobs",
            headers=H,
            files={"file": ("a.png", png)},
            data={"options": '{"output":"text"}'},
        )
        id = r.json()["job_id"]
        await app.state.jobs.jobs[id].task
        result = (await c.get(f"/v1/ocr/jobs/{id}/result", headers=H)).json()
        assert result["text"] and not result["markdown"] and result["pages"][0]["text"]
