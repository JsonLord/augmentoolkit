import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer

from .classify import features
from .config import Settings
from .http_security import Guard
from .ingest import decode_image, encode
from .openai_facade import ChatRequest, completion, image_from_request
from .schemas import OCROptions, RasterPage, SafeError

security = HTTPBearer()


def create_app(settings=None, backend=None, *, remote_enabled=False):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        nonlocal backend
        if backend is None:
            import os

            from .adapters.rapidocr_cpu import RapidCPU

            cpu_backend = RapidCPU(settings.models_dir)
            try:
                await cpu_backend.initialize()
            except SafeError:
                pass

            alias_backend = None
            openai_url = os.getenv("OPENAI_COMPATIBLE_URL")
            openai_key = os.getenv("OPENAI_COMPATIBLE_API")
            openai_model = os.getenv("OPENAI_COMPATIBLE_MODEL", "alias-ocr")

            if remote_enabled and openai_url and openai_key:
                from .adapters.freellm_alias import FreeLLMAlias

                alias_backend = FreeLLMAlias(openai_url, openai_key)
                alias_backend.actual_model = openai_model
                try:
                    await alias_backend.probe()
                except SafeError:
                    pass

            class AutoBackend:
                def __init__(self, cpu, alias, alias_model):
                    self.cpu = cpu
                    self.alias = alias
                    self.alias_model = alias_model
                    self.name = "auto"
                    self.destination = None
                    self.external = False

                async def health(self):
                    return await self.cpu.health()

                async def capabilities(self):
                    return await self.cpu.capabilities()

                async def recognize(self, page, options):
                    model_id = getattr(options, "_req_model", "ocr-cpu")
                    if model_id == "ocr-cpu":
                        return await self.cpu.recognize(page, options)
                    elif model_id == "ocr-alias":
                        if not self.alias:
                            raise SafeError("alias_unconfigured")
                        if not (await self.alias.health()).ready:
                            raise SafeError("alias_not_ready")
                        return await self.alias.recognize(page, options)
                    else:
                        try:
                            return await self.cpu.recognize(page, options)
                        except Exception:
                            if (
                                self.alias
                                and (await self.alias.health()).ready
                                and options.sensitivity == "D0"
                            ):
                                return await self.alias.recognize(page, options)
                            raise SafeError("model_not_ready") from None

            backend = AutoBackend(cpu_backend, alias_backend, openai_model)
        app.state.backend = backend
        try:
            yield
        finally:
            alias = getattr(backend, "alias", None)
            if alias is not None:
                await alias.close()

    app = FastAPI(
        docs_url="/docs",
        title="Authenticated PP-OCRv6 Small worker",
        version="1.0.0",
        lifespan=lifespan,
    )
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

    @app.get("/ready", dependencies=[Depends(security)])
    async def ready():
        h = await app.state.backend.health()
        return JSONResponse({"ready": h.ready}, status_code=200 if h.ready else 503)

    @app.get("/internal/capabilities", dependencies=[Depends(security)])
    async def capabilities():
        return await app.state.backend.capabilities()

    @app.post("/internal/ocr/page", dependencies=[Depends(security)])
    async def page(
        file: UploadFile = File(...),
        request_id: str = Form(""),
        sensitivity: str = Form(...),
        options: str = Form("{}"),
    ):
        if sensitivity != "D0":
            raise SafeError("invalid_worker_options")
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
            if not (await app.state.backend.health()).ready:
                raise SafeError("model_not_ready")
            return await app.state.backend.recognize(p, OCROptions(sensitivity=sensitivity))

    @app.get("/v1/models", dependencies=[Depends(security)])
    async def list_models():
        models = [
            {
                "id": "ocr-cpu",
                "object": "model",
                "created": 1677610602,
                "owned_by": "augmentoolkit",
            },
            {
                "id": "ocr-auto",
                "object": "model",
                "created": 1677610602,
                "owned_by": "augmentoolkit",
            },
        ]

        if hasattr(app.state.backend, "alias") and app.state.backend.alias:
            try:
                h = await app.state.backend.alias.health()
                if h.ready:
                    models.append(
                        {
                            "id": "ocr-alias",
                            "object": "model",
                            "created": 1677610602,
                            "owned_by": "augmentoolkit",
                        }
                    )
            except Exception:
                pass

        return {
            "object": "list",
            "data": models,
        }

    @app.get("/v1/ocr/capabilities", dependencies=[Depends(security)])
    async def v1_capabilities():
        return await app.state.backend.capabilities()

    @app.post("/v1/ocr/page", dependencies=[Depends(security)])
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

            from .classify import features
            from .ingest import decode_image, encode

            # Reject unsupported media by catching errors or doing a quick check
            try:
                image = await asyncio.to_thread(decode_image, data, settings.max_pixels)
            except Exception:
                raise SafeError("unsupported_media") from None

            data_enc = encode(image)
            p = RasterPage(
                0,
                data_enc,
                image.width,
                image.height,
                "agent-page",
                features=features(data_enc),
            )
            if not (await app.state.backend.health()).ready:
                raise SafeError("model_not_ready")
            return await app.state.backend.recognize(p, OCROptions(sensitivity="D0"))

    @app.post("/v1/chat/completions", dependencies=[Depends(security)])
    async def v1_chat_completions(req: ChatRequest):
        if req.model not in ("ocr-cpu", "ocr-alias", "ocr-auto"):
            raise SafeError("unsupported_model")

        if admission.locked():
            raise SafeError("worker_busy", True)

        async with admission:
            if not (await app.state.backend.health()).ready:
                raise SafeError("model_not_ready")

            try:
                data, ext = image_from_request(req, settings.max_upload)
            except SafeError as e:
                raise e
            except Exception:
                raise SafeError("unsupported_media") from None

            if (
                "sensitivity" not in req.ocr_options.model_fields_set
                or req.ocr_options.sensitivity != "D0"
            ):
                raise SafeError("explicit_d0_required")

            from .classify import features
            from .ingest import decode_image, encode

            try:
                image = await asyncio.to_thread(decode_image, data, settings.max_pixels)
            except Exception:
                raise SafeError("unsupported_media") from None

            data_enc = encode(image)
            p = RasterPage(
                0,
                data_enc,
                image.width,
                image.height,
                "agent-chat-page",
                features=features(data_enc),
            )

            opts = req.ocr_options
            if "sensitivity" not in opts.model_fields_set or opts.sensitivity != "D0":
                raise SafeError("explicit_d0_required")
            opts._req_model = req.model

            result = await app.state.backend.recognize(p, opts)
            return completion(result, req.model)

    return app


app = create_app()
