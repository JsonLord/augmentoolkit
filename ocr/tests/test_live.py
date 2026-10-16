import os

import pytest
from ocr.adapters.rapidocr_cpu import RapidCPU
from ocr.config import Settings
from ocr.fixtures.generate import raster
from ocr.ingest import encode, ingest
from ocr.routing import Dispatcher
from ocr.schemas import OCROptions


@pytest.mark.skipif(os.getenv("OCR_RUN_LIVE_CPU") != "1", reason="opt-in real CPU models")
async def test_real_cpu():
    s = Settings.from_env()
    cpu = RapidCPU(s.models_dir)
    await cpu.initialize()
    d = Dispatcher(cpu, None, s)
    d.start()
    try:
        page = ingest(encode(raster("Public OCR benchmark.")), "test.png", OCROptions(), s)[0]
        r = await d.page(page, OCROptions(mode="cpu_only"))
        assert "Public OCR benchmark" in r.text
        assert r.blocks and r.engine == "pp-ocrv6-small"
    finally:
        await d.close()


@pytest.mark.skipif(
    os.getenv("OCR_RUN_LIVE_ALIAS") != "1",
    reason="requires approved D0 endpoint and credentials",
)
async def test_live_alias():
    from ocr.adapters.freellm_alias import FreeLLMAlias

    s = Settings.from_env()
    a = FreeLLMAlias(s.freellm_url, s.freellm_key)
    try:
        report = await a.probe()
        assert report["multimodal_verified"]
        assert not report["d2_qualified"]
    finally:
        await a.close()


@pytest.mark.skipif(os.getenv("OCR_RUN_LIVE_CPU") != "1", reason="opt-in real CPU models")
async def test_real_api_image_and_pdf(tmp_path):
    import io

    import httpx
    from ocr.api import create_app
    from ocr.fixtures.generate import raster

    s = Settings(token="synthetic", models_dir=os.environ["OCR_MODELS_DIR"])
    cpu = RapidCPU(s.models_dir)
    await cpu.initialize()
    app = create_app(s, cpu)
    h = {"Authorization": "Bearer synthetic"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        im = raster("Public OCR benchmark.")
        image = encode(im)
        r = await c.post("/v1/ocr/page", headers=h, files={"file": ("public.png", image)})
        assert r.status_code == 200 and "Public OCR benchmark" in r.json()["text"]
        pdf = io.BytesIO()
        im.save(pdf, format="PDF", resolution=200)
        r = await c.post("/v1/ocr/jobs", headers=h, files={"file": ("public.pdf", pdf.getvalue())})
        id = r.json()["job_id"]
        await app.state.jobs.jobs[id].task
        r = await c.get(f"/v1/ocr/jobs/{id}/result", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "completed"
        assert (
            "Public OCR benchmark" in r.json()["text"]
            and r.json()["pages"][0]["source"] == "cpu_ocr"
        )
