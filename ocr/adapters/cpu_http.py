import httpx

from ..schemas import BackendCapabilities, BackendHealth, OCRPageResult, SafeError
from .http import request_json


class RemoteCPU:
    name = "cpu"
    external = True

    def __init__(self, url, token, timeout=90, transport=None):
        if not token:
            raise ValueError("worker_token_required")
        self.url = url
        self.destination = url
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={"Authorization": "Bearer " + token},
        )

    async def close(self):
        await self.client.aclose()

    async def capabilities(self):
        # Conservative local contract; don't inherit a remote service's sensitive-class claims.
        return BackendCapabilities(
            name="cpu",
            bounding_boxes=True,
            languages=["de", "en"],
            supports_sensitive_classes=["D0"],
            multimodal_verified=True,
        )

    async def health(self):
        try:
            r = await request_json(self.client, "GET", self.url + "/ready", limit=4096)
            return BackendHealth(ready=r.get("ready") is True)
        except SafeError:
            return BackendHealth(ready=False, code="cpu_worker_unavailable")

    async def recognize(self, page, options):
        r = await request_json(
            self.client,
            "POST",
            self.url + "/internal/ocr/page",
            files={"file": ("page.png", page.image, "image/png")},
            data={"request_id": page.page_id, "options": "{}"},
        )
        try:
            result = OCRPageResult.model_validate(r)
        except Exception:
            raise SafeError("invalid_cpu_response") from None
        if result.source != "cpu_ocr" or result.engine != "pp-ocrv6-small":
            raise SafeError("unexpected_cpu_engine")
        if result.image_width != page.width or result.image_height != page.height:
            raise SafeError("cpu_geometry_mismatch")
        result.page_index = page.index
        result.provider_destination = self.destination
        return result
