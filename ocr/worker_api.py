import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, UploadFile, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi

from .schemas import OCROptions, RasterPage, SafeError
from .openai_facade import ChatRequest, image_from_request, completion

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


    @app.get("/v1/models")
    async def list_models():
        return {
            "object": "list",
            "data": [
                {"id": "ocr-cpu", "object": "model", "created": 1677610602, "owned_by": "augmentoolkit"}
            ]
        }

    @app.get("/v1/ocr/capabilities")
    async def v1_capabilities():
        return await backend.capabilities()

    @app.post("/v1/ocr/page")
    async def v1_page(
        file: UploadFile = File(...),
        sensitivity: str = Form(...),
        options: str = Form("{}"),
    ):
        if sensitivity != "D0":
            raise SafeError("invalid_worker_options", False)

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

            from .ingest import decode_image, encode
            from .classify import features

            # Reject unsupported media by catching errors or doing a quick check
            try:
                image = await asyncio.to_thread(decode_image, data, settings.max_pixels)
            except Exception:
                raise SafeError("unsupported_media")

            data_enc = encode(image)
            p = RasterPage(
                0,
                data_enc,
                image.width,
                image.height,
                "agent-page",
                features=features(data_enc),
            )
            if not (await backend.health()).ready:
                raise SafeError("model_not_ready")
            return await backend.recognize(p, OCROptions(sensitivity="D0"))

    @app.post("/v1/chat/completions")
    async def v1_chat_completions(req: ChatRequest):
        if req.model != "ocr-cpu":
            raise SafeError("unsupported_model")

        if admission.locked():
            raise SafeError("worker_busy", True)

        async with admission:
            if not (await backend.health()).ready:
                raise SafeError("model_not_ready")

            try:
                data, ext = image_from_request(req, settings.max_upload)
            except SafeError as e:
                raise e
            except Exception:
                raise SafeError("unsupported_media")

            from .ingest import decode_image, encode
            from .classify import features

            try:
                image = await asyncio.to_thread(decode_image, data, settings.max_pixels)
            except Exception:
                raise SafeError("unsupported_media")

            data_enc = encode(image)
            p = RasterPage(
                0,
                data_enc,
                image.width,
                image.height,
                "agent-chat-page",
                features=features(data_enc),
            )

            # Force sensitivity to D0
            opts = req.ocr_options
            opts.sensitivity = "D0"

            result = await backend.recognize(p, opts)
            return completion(result, req.model)


    return app


app = create_app()
