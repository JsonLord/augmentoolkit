import asyncio
import io

import pytest
from ocr.schemas import BackendCapabilities, BackendHealth, Block, OCRPageResult
from PIL import Image


@pytest.fixture
def png():
    b = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(b, format="PNG")
    return b.getvalue()


class Fake:
    external = False

    def __init__(
        self,
        name="cpu",
        *,
        text="Public fixture text",
        delay=0,
        fail=None,
        confidence=0.98,
        external=False,
    ):
        self.name = name
        self.text = text
        self.delay = delay
        self.fail = fail
        self.confidence = confidence
        self.calls = []
        self.external = external
        self.destination = "fake-local" if not external else "https://unapproved.invalid"
        self.qualified = True
        self.ready = True

    async def capabilities(self):
        return BackendCapabilities(name=self.name, text=True, multimodal_verified=True)

    async def health(self):
        return BackendHealth(ready=self.ready)

    async def recognize(self, page, options):
        self.calls.append(page.index)
        await asyncio.sleep(self.delay * (3 - page.index % 3))
        if self.fail:
            error = self.fail() if callable(self.fail) else self.fail
            if error:
                raise error
        return OCRPageResult(
            page_index=page.index,
            source="cpu_ocr" if self.name == "cpu" else "alias_ocr",
            text=self.text + str(page.index),
            markdown=self.text + str(page.index),
            engine="test-only",
            provider_destination=self.destination,
            image_width=page.width,
            image_height=page.height,
            blocks=[
                Block(
                    text=self.text,
                    confidence=self.confidence if self.name == "cpu" else None,
                    confidence_kind="test-only",
                )
            ],
        )
