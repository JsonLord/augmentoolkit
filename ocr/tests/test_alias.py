import json

import httpx
import pytest
from ocr.adapters.freellm_alias import FreeLLMAlias
from ocr.normalize import grounding, polygon
from ocr.schemas import OCROptions, RasterPage, SafeError


@pytest.mark.parametrize(
    "response,code",
    [
        (
            {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
            "alias_empty",
        ),
        (
            {"choices": [{"finish_reason": "length", "message": {"content": "some"}}]},
            "alias_truncated",
        ),
        (
            {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
            "alias_empty",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": [{"type": "text", "text": "some"}]},
                    }
                ]
            },
            "alias_empty",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "<think>text</think>"},
                    }
                ]
            },
            "alias_reasoning_output",
        ),
    ],
)
async def test_invalid_reply(png, response, code):
    def handle(r):
        data = json.loads(r.content)
        assert data["model"] == "alias-ocr" and data["stream"] is False
        assert data["messages"][0]["content"][0]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        assert "enable_thinking" not in data
        return httpx.Response(200, json=response)

    a = FreeLLMAlias("http://127.0.0.1:1/v1", "fake", transport=httpx.MockTransport(handle))
    a.qualified = True
    a.qualified_at = __import__("time").monotonic()
    try:
        with pytest.raises(SafeError, match=code):
            await a.recognize(RasterPage(0, png, 200, 120, "id"), OCROptions())
    finally:
        await a.close()


@pytest.mark.parametrize("status", [429, 502, 503])
async def test_retryable(status, png):
    a = FreeLLMAlias(
        "http://127.0.0.1:1/v1",
        "fake",
        transport=httpx.MockTransport(lambda r: httpx.Response(status)),
    )
    try:
        with pytest.raises(SafeError) as exc:
            await a._completion(png)
        assert exc.value.retryable
    finally:
        await a.close()


def test_grounding_and_boxes():
    assert grounding("![table](0,10,500,1000)")[0].confidence is None
    with pytest.raises(SafeError):
        grounding("![text](-1,0,2,3)")
    with pytest.raises(SafeError):
        grounding("![text](nan,0,2,3)")
    p, b = polygon([(0, 0), (200, 0), (200, 100), (0, 100)], 200, 100)
    assert b == [0, 0, 1000, 1000]


def test_untrusted_geometry_rejected():
    from ocr.schemas import Block
    from pydantic import ValidationError

    for box in ([1, 2, 3], [-1, 2, 3, 4], [5, 5, 2, 2], [0, 0, float("nan"), 3]):
        with pytest.raises(ValidationError):
            Block(bbox_1000=box)
    assert Block(text="plain Markdown").bbox_1000 is None


@pytest.mark.parametrize("valid", [True, False])
async def test_probe_does_not_qualify_destination(monkeypatch, valid):
    monkeypatch.setattr("secrets.token_hex", lambda n: "abcd99")

    def handle(request):
        body = json.loads(request.content)
        assert "ABCD99" not in body["messages"][0]["content"][1]["text"]
        return httpx.Response(
            200,
            json={
                "model": "lightonai/LightOnOCR-3-4B",
                "provider": "helmholtz",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "OCR ABCD99" if valid else "empty white image"},
                    }
                ],
            },
        )

    a = FreeLLMAlias("http://127.0.0.1:1/v1", "fake", transport=httpx.MockTransport(handle))
    try:
        if valid:
            report = await a.probe()
            assert report["multimodal_verified"] and report["observed_provider"] == "helmholtz"
            assert not report["d2_qualified"] and not report["effective_destination_verified"]
            assert not (await a.capabilities()).grounding_verified
            from ocr.config import Settings
            from ocr.security import authorize

            with pytest.raises(SafeError, match="no_authorized_destination"):
                authorize(
                    a,
                    OCROptions(sensitivity="D2"),
                    Settings(gpu_enabled=True),
                    await a.capabilities(),
                )
        else:
            with pytest.raises(SafeError, match="alias_multimodal_probe_failed"):
                await a.probe()
            assert not a.qualified
    finally:
        await a.close()
