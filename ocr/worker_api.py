import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .classify import features
from .config import Settings
from .http_security import Guard
from .ingest import decode_image, encode
from .schemas import OCROptions, RasterPage, SafeError


def create_app(settings=None, backend=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        nonlocal backend
        if backend is None:
            from .adapters.rapidocr_cpu import RapidCPU

            backend = RapidCPU(settings.models_dir)
            try:
                await backend.initialize()
            except SafeError:
                pass
        app.state.backend = backend
        yield

    app = FastAPI(title="Authenticated PP-OCRv6 Small worker", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        Guard,
        token=settings.worker_token,
        max_bytes=settings.max_upload,
        rate=settings.rate_per_minute,
    )
    admission = asyncio.Semaphore(1)

    @app.exception_handler(SafeError)
    async def safe(request, exc):
        return JSONResponse(
            {"code": exc.code},
            status_code=503 if exc.code in ("model_not_ready", "worker_busy") else 422,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"code": "invalid_request"}, status_code=422)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        h = await backend.health()
        return JSONResponse({"ready": h.ready}, status_code=200 if h.ready else 503)

    @app.get("/internal/capabilities")
    async def capabilities():
        return await backend.capabilities()

    @app.post("/internal/ocr/page")
    async def page(
        file: UploadFile = File(...),
        request_id: str = Form(""),
        options: str = Form("{}"),
    ):
        # Reject rather than grow an unbounded HTTP queue on a two-core worker.
        if admission.locked():
            await file.close()
            raise SafeError("worker_busy", True)
        async with admission:
            try:
                data = await file.read(settings.max_upload + 1)
            finally:
                await file.close()
            if len(data) > settings.max_upload:
                raise SafeError("upload_limit")
            if len(request_id) > 128 or options != "{}":
                raise SafeError("invalid_worker_options")
            image = await asyncio.to_thread(decode_image, data, settings.max_pixels)
            data = encode(image)
            p = RasterPage(
                0,
                data,
                image.width,
                image.height,
                "worker-page",
                features=features(data),
            )
            if not (await backend.health()).ready:
                raise SafeError("model_not_ready")
            return await backend.recognize(p, OCROptions())

    return app


app = create_app()
