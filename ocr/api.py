"""Private dispatcher ASGI app. Does not bind a listener or change production."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings
from .http_security import Guard, owner
from .jobs import Jobs, status
from .routing import Dispatcher
from .schemas import OCROptions, SafeError
from .security import sensitivity

CODES = {
    "unauthorized": 401,
    "job_not_found": 404,
    "no_authorized_destination": 403,
    "idempotency_conflict": 409,
    "result_not_ready": 409,
    "job_capacity": 429,
    "queue_full": 429,
    "upload_limit": 413,
    "model_not_ready": 503,
}


def create_app(settings=None, cpu=None, gpu=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        nonlocal cpu, gpu
        probe_task = None
        if cpu is None:
            if settings.cpu_url:
                from .adapters.cpu_http import RemoteCPU

                cpu = RemoteCPU(settings.cpu_url, settings.worker_token, settings.timeout)
            else:
                from .adapters.rapidocr_cpu import RapidCPU

                cpu = RapidCPU(settings.models_dir)
                try:
                    await cpu.initialize()
                except SafeError:
                    pass  # /ready reports failure; never pretend warmed readiness.
        if gpu is None and settings.gpu_enabled and settings.freellm_url and settings.freellm_key:
            from .adapters.freellm_alias import FreeLLMAlias

            gpu = FreeLLMAlias(settings.freellm_url, settings.freellm_key, settings.timeout)

            async def probe():
                try:
                    await gpu.probe()
                except Exception:
                    gpu.qualified = False

            import asyncio

            probe_task = asyncio.create_task(probe())
        dispatcher = Dispatcher(cpu, gpu, settings)
        dispatcher.start()
        jobs = Jobs(dispatcher, settings)
        jobs.start()
        app.state.jobs = jobs
        app.state.dispatcher = dispatcher
        try:
            yield
        finally:
            if probe_task:
                probe_task.cancel()
                await asyncio.gather(probe_task, return_exceptions=True)
            await jobs.close()
            await dispatcher.close()

    app = FastAPI(title="Private Hybrid OCR", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        Guard,
        token=settings.token,
        max_bytes=settings.max_upload,
        rate=settings.rate_per_minute,
    )

    @app.exception_handler(SafeError)
    async def safe_error(request, exc):
        return JSONResponse({"code": exc.code}, status_code=CODES.get(exc.code, 422))

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"code": "invalid_request"}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return JSONResponse({"code": "internal_error"}, status_code=500)

    def parse_options(raw):
        try:
            if len(raw) > 8192:
                raise ValueError()
            opts = OCROptions.model_validate_json(raw)
            opts.sensitivity = sensitivity(opts.sensitivity, settings.min_sensitivity)
            opts.max_pages = min(opts.max_pages, settings.max_pages)
            return opts
        except Exception:
            raise SafeError("invalid_options") from None

    async def upload(file):
        try:
            data = await file.read(settings.max_upload + 1)
        finally:
            await file.close()
        if len(data) > settings.max_upload:
            raise SafeError("upload_limit")
        return data

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        cpu_health = await app.state.dispatcher.cpu.backend.health()
        return JSONResponse(
            {"ready": cpu_health.ready}, status_code=200 if cpu_health.ready else 503
        )

    @app.post("/v1/ocr/jobs", status_code=202)
    async def submit(request: Request, file: UploadFile = File(...), options: str = Form("{}")):
        opts = parse_options(options)
        data = await upload(file)
        key = request.headers.get("idempotency-key")
        if key and len(key) > 128:
            raise SafeError("invalid_idempotency_key")
        j = app.state.jobs.submit(data, file.filename or "", opts, owner(request), key)
        return dict(
            job_id=j.id,
            status=j.status,
            request_id=j.request_id,
            status_url=f"/v1/ocr/jobs/{j.id}",
            result_url=f"/v1/ocr/jobs/{j.id}/result",
        )

    @app.get("/v1/ocr/jobs/{job_id}")
    async def get(job_id: str, request: Request):
        return status(app.state.jobs.get(job_id, owner(request)))

    @app.get("/v1/ocr/jobs/{job_id}/result")
    async def result(job_id: str, request: Request):
        j = app.state.jobs.get(job_id, owner(request))
        if j.result is None:
            raise SafeError("result_not_ready")
        return j.result

    @app.delete("/v1/ocr/jobs/{job_id}")
    async def cancel(job_id: str, request: Request):
        j = app.state.jobs.get(job_id, owner(request))
        app.state.jobs.cancel(j)
        return status(j)

    @app.post("/v1/ocr/page")
    async def page(request: Request, file: UploadFile = File(...), options: str = Form("{}")):
        opts = parse_options(options)
        opts.max_pages = 1
        pages = await app.state.jobs.parse(await upload(file), file.filename or "", opts)
        return await app.state.dispatcher.page(pages[0], opts)

    @app.get("/metrics")
    async def metrics():
        import numpy as np

        lanes = [app.state.dispatcher.cpu] + (
            [app.state.dispatcher.gpu] if app.state.dispatcher.gpu else []
        )
        return {
            lane.backend.name: dict(
                queued=lane.queue.qsize(),
                active=lane.active,
                completed=lane.completed,
                errors=lane.errors,
                ewma_ms=lane.ewma,
                cold_ms=lane.cold_ms,
                warm_latency_ms={
                    str(p): float(np.percentile(lane.samples, p)) if lane.samples else None
                    for p in (50, 95, 99)
                },
            )
            for lane in lanes
        }

    from .openai_facade import ChatRequest, completion, image_from_request

    @app.post("/v1/chat/completions")
    async def chat(body: ChatRequest):
        data, filename = image_from_request(body, settings.max_upload)
        opts = body.ocr_options.model_copy(deep=True)
        opts.sensitivity = sensitivity(opts.sensitivity, settings.min_sensitivity)
        opts.mode = {"ocr-auto": "auto", "ocr-cpu": "cpu_only", "ocr-gpu": "gpu_only"}[body.model]
        opts.max_pages = 1
        pages = await app.state.jobs.parse(data, filename, opts)
        result = await app.state.dispatcher.page(pages[0], opts)
        return completion(result, body.model)

    return app


app = create_app()
