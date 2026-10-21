import asyncio

import httpx
from conftest import Fake
from ocr.api import create_app
from ocr.config import Settings
from ocr.fixtures.generate import generate
from ocr.worker_api import create_app as worker_app

H = {"Authorization": "Bearer test"}


async def wait(client, id):
    for _ in range(200):
        s = (await client.get("/v1/ocr/jobs/" + id, headers=H)).json()
        if s["status"] in ("completed", "partial", "failed", "cancelled"):
            return s
        await asyncio.sleep(0.02)
    raise AssertionError("job did not finish")


async def test_auth_downgrade_idempotency_cancel(png):
    cpu = Fake()
    app = create_app(Settings(token="test"), cpu)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        assert (await c.post("/v1/ocr/jobs", content=b"not multipart")).status_code == 401
        r = await c.post(
            "/v1/ocr/jobs",
            headers={**H, "Idempotency-Key": "key"},
            files={"file": ("a.png", png)},
            data={"options": '{"sensitivity":"D0"}'},
        )
        assert r.status_code == 202
        id = r.json()["job_id"]
        assert (await wait(c, id))["status"] == "completed"
        result = (await c.get(f"/v1/ocr/jobs/{id}/result", headers=H)).json()
        assert result["sensitivity"] == "D2"
        assert result["pages"][0]["page_index"] == 0
        again = await c.post(
            "/v1/ocr/jobs",
            headers={**H, "Idempotency-Key": "key"},
            files={"file": ("a.png", png)},
            data={"options": '{"sensitivity":"D0"}'},
        )
        assert again.json()["job_id"] == id
        bad = await c.post(
            "/v1/ocr/jobs",
            headers={**H, "Idempotency-Key": "key"},
            files={"file": ("a.png", png)},
            data={"options": '{"sensitivity":"D2","force_ocr":true}'},
        )
        assert bad.status_code == 409
        assert (await c.get(f"/v1/ocr/jobs/{id}/result")).status_code == 401
        await c.delete(f"/v1/ocr/jobs/{id}", headers=H)
        assert (await c.get(f"/v1/ocr/jobs/{id}/result", headers=H)).status_code == 409


async def test_out_of_order_mixed_and_partial(tmp_path):
    generate(tmp_path)
    cpu = Fake(delay=0.01)
    app = create_app(Settings(token="test"), cpu)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        r = await c.post(
            "/v1/ocr/jobs",
            headers=H,
            files={"file": ("a.pdf", (tmp_path / "mixed-20.pdf").read_bytes())},
        )
        id = r.json()["job_id"]
        s = await wait(c, id)
        assert s["status"] == "completed"
        result = (await c.get(f"/v1/ocr/jobs/{id}/result", headers=H)).json()
        assert [p["page_index"] for p in result["pages"]] == list(range(20))
        assert "<!-- page:1 -->" in result["markdown"] and "<!-- page:20 -->" in result["markdown"]
        assert len(cpu.calls) == 10


async def test_worker_auth_limits_ready(png):
    app = worker_app(Settings(worker_token="test"), Fake())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/ready")).status_code == 401
        assert (await c.get("/ready", headers=H)).json()["ready"]
        assert (
            await c.post(
                "/internal/ocr/page",
                headers=H,
                files={"file": ("a.png", png)},
                data={"sensitivity": "D0"},
            )
        ).status_code == 200
        assert (
            await c.post("/internal/ocr/page", headers=H, files={"file": ("a.png", b"%PDF-")})
        ).status_code == 422


async def test_ttl_and_capacity(png):
    app = create_app(Settings(token="test", ttl=0.1, active_jobs=1), Fake(delay=0.5))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        r = await c.post("/v1/ocr/jobs", headers=H, files={"file": ("a.png", png)})
        id = r.json()["job_id"]
        assert (
            await c.post("/v1/ocr/jobs", headers=H, files={"file": ("a.png", png)})
        ).status_code == 429
        await asyncio.sleep(0.15)
        assert (await c.get("/v1/ocr/jobs/" + id, headers=H)).status_code == 404


async def test_two_lane_out_of_order_assembly():
    import io

    from PIL import Image

    completed = []

    class Record(Fake):
        async def recognize(self, page, options):
            r = await super().recognize(page, options)
            completed.append(page.index)
            return r

    gpu = Record("gpu", delay=0.02)
    app = create_app(
        Settings(
            token="test",
            min_sensitivity="D0",
            gpu_enabled=True,
            overflow_enabled=True,
            overflow_qualified=True,
        ),
        Fake(delay=0.5),
        gpu,
    )
    b = io.BytesIO()
    im = Image.new("RGB", (100, 100), "white")
    im.save(b, format="TIFF", save_all=True, append_images=[im, im])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        app.state.dispatcher.cpu.ewma = 10000
        app.state.dispatcher.gpu.ewma = 1
        r = await c.post(
            "/v1/ocr/jobs",
            headers=H,
            files={"file": ("test.tiff", b.getvalue())},
            data={"options": '{"sensitivity":"D0","mode":"throughput","allow_gpu_overflow":true}'},
        )
        id = r.json()["job_id"]
        await app.state.jobs.jobs[id].task
        r = (await c.get(f"/v1/ocr/jobs/{id}/result", headers=H)).json()
        assert r["status"] == "completed" and [p["page_index"] for p in r["pages"]] == [0, 1, 2]
        assert completed != [0, 1, 2] and len(gpu.calls) == 3
