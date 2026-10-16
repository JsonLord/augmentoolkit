import base64
import io
import time

import httpx
from PIL import Image, ImageDraw

from ..normalize import grounding
from ..schemas import BackendCapabilities, BackendHealth, OCRPageResult, SafeError
from .http import request_json


class FreeLLMAlias:
    name = "gpu"
    external = True

    def __init__(self, url, key, timeout=90, transport=None):
        if not url or not key:
            raise ValueError("alias_configuration_missing")
        self.url = url
        self.destination = None
        self.qualified = False
        self.qualified_at = 0
        self.actual_model = None
        self.grounding_verified = False
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={"Authorization": "Bearer " + key},
        )

    async def close(self):
        await self.client.aclose()

    async def capabilities(self):
        return BackendCapabilities(
            name="gpu",
            layout_blocks=self.grounding_verified,
            bounding_boxes=self.grounding_verified,
            grounding_verified=self.grounding_verified,
            multimodal_verified=self.qualified,
            effective_destination_verifiable=False,
            supports_sensitive_classes=["D0"] if self.qualified else [],
        )

    async def health(self):
        return BackendHealth(
            ready=self.qualified and time.monotonic() - self.qualified_at < 3600,
            code="ok" if self.qualified else "alias_unqualified",
        )

    async def _completion(self, image, *, probe=False):
        # Separate copy: CPU source raster and coordinate system remain untouched.
        with Image.open(io.BytesIO(image)) as im:
            im = im.convert("RGB")
            im.thumbnail((2048, 2048))
            b = io.BytesIO()
            im.save(b, format="PNG")
        content = [
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
                },
            }
        ]
        # No unverified provider-specific options, tools or thinking flags.
        content.append(
            {
                "type": "text",
                "text": "Transcribe the document verbatim. Return only document text or Markdown.",
            }
        )
        data = await request_json(
            self.client,
            "POST",
            self.url + "/chat/completions",
            json={
                "model": "alias-ocr",
                "messages": [{"role": "user", "content": content}],
                "stream": False,
                "temperature": 0.1,
            },
        )
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
            if choice.get("finish_reason") not in ("stop",):
                raise SafeError("alias_truncated")
            if not isinstance(text, str) or not text.strip():
                raise SafeError("alias_empty")
            if "<think>" in text or "<analysis>" in text:
                raise SafeError("alias_reasoning_output")
            if len(text) > 2_000_000:
                raise SafeError("backend_response_limit")
            model = data.get("model")
            # Response metadata is recorded as an observation, NEVER proof of enforced routing.
            observed = data.get("provider") if isinstance(data.get("provider"), str) else None
            return (
                text,
                model if isinstance(model, str) and model != "alias-ocr" else None,
                observed,
            )
        except SafeError:
            raise
        except (KeyError, IndexError, TypeError):
            raise SafeError("alias_invalid_response") from None

    async def probe(self):
        self.qualified = False
        # Synthetic D0 challenge text is not included in the prompt; a text-only echo cannot pass.
        import secrets

        challenge = "OCR " + secrets.token_hex(3).upper()
        im = Image.new("RGB", (800, 160), "white")
        d = ImageDraw.Draw(im)
        from PIL import ImageFont

        font = ImageFont.truetype("DejaVuSans.ttf", 48)
        d.text((25, 40), challenge, fill="black", font=font)
        b = io.BytesIO()
        im.save(b, format="PNG")
        text, model, provider = await self._completion(b.getvalue(), probe=True)
        if challenge.casefold() not in text.casefold():
            raise SafeError("alias_multimodal_probe_failed")
        self.qualified = True
        self.qualified_at = time.monotonic()
        self.actual_model = model
        # Observed metadata is not a verified effective destination/fallback guarantee.
        return {
            "multimodal_verified": True,
            "observed_model": model,
            "observed_provider": provider,
            "effective_destination_verified": False,
            "grounding_verified": False,
            "d2_qualified": False,
        }

    async def recognize(self, page, options):
        if not (await self.health()).ready:
            raise SafeError("alias_unqualified")
        text, model, provider = await self._completion(page.image)
        # Model changes revoke prior probe qualification, even for D0.
        if model != self.actual_model:
            self.qualified = False
            raise SafeError("alias_model_changed")
        blocks = grounding(text) if self.grounding_verified else []
        return OCRPageResult(
            page_index=page.index,
            source="alias_ocr",
            engine=model,
            provider_destination=None,
            text=text,
            markdown=text,
            image_width=page.width,
            image_height=page.height,
            blocks=blocks,
            flags=["destination_unverified"] + ([] if blocks else ["boxes_unavailable"]),
        )
