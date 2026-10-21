import asyncio
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from ..normalize import polygon
from ..schemas import (
    BackendCapabilities,
    BackendHealth,
    Block,
    OCRPageResult,
    SafeError,
)


class RapidCPU:
    name = "cpu"
    destination = "local-cpu"
    external = False

    def __init__(self, models_dir):
        self.models_dir = Path(models_dir)
        self.engine = None
        self.lock = asyncio.Lock()

    def _initialize(self):
        import cv2
        from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR

        cv2.setNumThreads(2)

        manifest = json.loads((Path(__file__).parents[1] / "models.json").read_text())
        params = {
            "Global.use_cls": False,
            "Global.log_level": "critical",
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.use_cuda": False,
        }
        for kind in ("Det", "Rec"):
            item = manifest[kind]
            path = self.models_dir / item["filename"]
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
            ):
                raise SafeError("model_integrity_unavailable")
            params.update(
                {
                    f"{kind}.model_path": str(path),
                    f"{kind}.model_type": ModelType.SMALL,
                    f"{kind}.ocr_version": OCRVersion.PPOCRV6,
                    f"{kind}.engine_type": EngineType.ONNXRUNTIME,
                }
            )
        self.engine = RapidOCR(params=params)
        # Force lazy models to initialize before readiness; never download implicitly.
        self.engine(np.full((96, 256, 3), 255, dtype=np.uint8), use_cls=False)
        self.engine(
            np.full((48, 256, 3), 255, dtype=np.uint8),
            use_det=False,
            use_cls=False,
            use_rec=True,
        )
        self.engine.update_params(use_det=True, use_cls=False, use_rec=True)
        for model in (self.engine.text_det, self.engine.text_rec):
            if model is None or model.session.session.get_providers() != ["CPUExecutionProvider"]:
                self.engine = None
                raise SafeError("unexpected_cpu_execution_provider")

    async def initialize(self):
        async with self.lock:
            if self.engine is None:
                try:
                    await asyncio.to_thread(self._initialize)
                except SafeError:
                    raise
                except Exception:
                    self.engine = None
                    raise SafeError("cpu_initialization_failed") from None

    async def capabilities(self):
        return BackendCapabilities(
            name="cpu",
            bounding_boxes=True,
            languages=["de", "en"],
            supports_sensitive_classes=["D0", "D1", "D2"],
            effective_destination_verifiable=True,
            multimodal_verified=self.engine is not None,
        )

    async def health(self):
        return BackendHealth(
            ready=self.engine is not None,
            code="ok" if self.engine else "model_not_ready",
        )

    def _recognize(self, page):
        image = np.asarray(Image.open(io.BytesIO(page.image)).convert("RGB"))[:, :, ::-1].copy()
        out = self.engine(image, use_cls=False)
        blocks = []
        if out.txts is not None:
            for text, score, box in zip(out.txts, out.scores, out.boxes, strict=True):
                p, b = polygon(box, page.width, page.height)
                blocks.append(
                    Block(
                        text=text,
                        confidence=float(score),
                        confidence_kind="rapidocr-recognition",
                        polygon_1000=p,
                        bbox_1000=b,
                    )
                )
        # Preserve engine reading order; multi-column order is explicitly flagged by quality checks.
        text = "\n".join(b.text for b in blocks)
        return OCRPageResult(
            page_index=page.index,
            source="cpu_ocr",
            engine="pp-ocrv6-small",
            backend_version=f"rapidocr/{importlib.metadata.version('rapidocr')};onnxruntime/{importlib.metadata.version('onnxruntime')}",
            provider_destination=self.destination,
            text=text,
            markdown=text,
            image_width=page.width,
            image_height=page.height,
            blocks=blocks,
        )

    async def recognize(self, page, options):
        if self.engine is None:
            raise SafeError("model_not_ready")
        # Shield native work and retain the lock through cancellation: ORT cannot be force-cancelled.
        async with self.lock:
            task = asyncio.create_task(asyncio.to_thread(self._recognize, page))
            try:
                result = await asyncio.shield(task)
                result.sensitivity = options.sensitivity
                result.page_id = page.page_id
                return result
            except asyncio.CancelledError:
                await task
                raise
            except Exception:
                raise SafeError("cpu_inference_failed") from None
