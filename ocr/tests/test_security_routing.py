import asyncio

import pytest
from conftest import Fake
from ocr.config import Settings
from ocr.routing import Dispatcher
from ocr.schemas import OCROptions, RasterPage, SafeError
from ocr.security import authorize, sensitivity


@pytest.mark.parametrize("level", ["D0", "D1", "D2"])
@pytest.mark.parametrize("name,external", [("cpu", False), ("cpu", True), ("gpu", True)])
async def test_security_matrix(level, name, external):
    b = Fake(name, external=external)
    opts = OCROptions(sensitivity=level)
    settings = Settings(gpu_enabled=True)
    if external and level != "D0":
        with pytest.raises(SafeError, match="no_authorized_destination"):
            authorize(b, opts, settings, await b.capabilities())
        assert not b.calls
    else:
        authorize(b, opts, settings, await b.capabilities())


async def test_d2_hidden_substitution_blocked(png):
    cpu = Fake("cpu", external=True)
    gpu = Fake("gpu", external=True)
    d = Dispatcher(cpu, gpu, Settings(gpu_enabled=True, external_cpu_classes=("D0", "D2")))
    d.start()
    try:
        with pytest.raises(SafeError, match="no_authorized_destination"):
            await d.page(
                RasterPage(0, png, 200, 120, "id"),
                OCROptions(mode="gpu_only", sensitivity="D2"),
            )
        assert not gpu.calls and not cpu.calls
    finally:
        await d.close()


async def test_low_confidence_escalation(png):
    cpu = Fake(confidence=0.2)
    gpu = Fake("gpu", text="Alternate transcription")
    d = Dispatcher(cpu, gpu, Settings(gpu_enabled=True))
    d.start()
    try:
        result = await d.page(RasterPage(0, png, 200, 120, "id"), OCROptions(sensitivity="D0"))
        assert (
            gpu.calls
            and result.alternatives
            and "needs_review" in result.flags
            or result.alternatives[0].flags
        )
        assert result.attempt_count == 2
    finally:
        await d.close()


@pytest.mark.parametrize("mode", ["cpu_only", "gpu_only"])
async def test_explicit_no_fallback(png, mode):
    cpu = Fake(fail=SafeError("failure"))
    gpu = Fake("gpu", fail=SafeError("failure"))
    d = Dispatcher(cpu, gpu, Settings(gpu_enabled=True))
    d.start()
    try:
        with pytest.raises(SafeError):
            await d.page(
                RasterPage(0, png, 200, 120, "id"),
                OCROptions(mode=mode, sensitivity="D0"),
            )
        assert bool(cpu.calls) == (mode == "cpu_only")
        assert bool(gpu.calls) == (mode == "gpu_only")
    finally:
        await d.close()


async def test_retry_policy_rechecked(png):
    cpu = Fake(external=True)

    def fail():
        cpu.external = True
        return SafeError("backend_http_503", True)

    cpu.fail = fail
    settings = Settings()
    d = Dispatcher(cpu, None, settings)
    d.start()
    try:
        task = asyncio.create_task(
            d.page(RasterPage(0, png, 200, 120, "id"), OCROptions(sensitivity="D0"))
        )
        await asyncio.sleep(0.04)
        settings.external_cpu_classes = ()
        with pytest.raises(SafeError):
            await task
        assert len(cpu.calls) == 1
    finally:
        await d.close()


async def test_fallback_and_throughput_gates(png):
    cpu = Fake(fail=SafeError("cpu_unavailable"))
    gpu = Fake("gpu")
    d = Dispatcher(cpu, gpu, Settings(gpu_enabled=True))
    d.start()
    try:
        r = await d.page(RasterPage(0, png, 200, 120, "id"), OCROptions(sensitivity="D0"))
        assert r.source == "alias_ocr" and "fallback_used" in r.flags
        cpu.fail = None
        cpu.calls.clear()
        gpu.calls.clear()
        d.cpu.ewma = 1000
        d.gpu.ewma = 10
        opts = OCROptions(mode="throughput", allow_gpu_overflow=True, sensitivity="D0")
        await d.page(RasterPage(0, png, 200, 120, "id"), opts)
        assert cpu.calls and not gpu.calls
        d.settings.overflow_enabled = True
        d.settings.overflow_qualified = True
        await d.page(RasterPage(1, png, 200, 120, "id"), opts)
        assert gpu.calls
    finally:
        await d.close()


def test_no_downgrade():
    assert sensitivity("D0", "D2") == "D2"


@pytest.mark.parametrize("status", [429, 502, 503])
async def test_bounded_retries_breaker(png, status):
    cpu = Fake(fail=SafeError(f"backend_http_{status}", True))
    d = Dispatcher(cpu, None, Settings())
    d.start()
    try:
        p = RasterPage(0, png, 200, 120, "id")
        with pytest.raises(SafeError):
            await d.page(p, OCROptions())
        assert len(cpu.calls) == 2
        with pytest.raises(SafeError):
            await d.page(p, OCROptions())
        assert len(cpu.calls) == 3
        with pytest.raises(SafeError):
            await d.page(p, OCROptions())
        assert len(cpu.calls) == 3 and d.cpu.open_until > 0
    finally:
        await d.close()


async def test_budget_health_and_gpu_fallback(png):
    cpu = Fake()
    gpu = Fake("gpu", fail=SafeError("alias_bad_reply"))
    d = Dispatcher(cpu, gpu, Settings(gpu_enabled=True, gpu_budget=1))
    d.start()
    try:
        p = RasterPage(0, png, 200, 120, "id")
        o = OCROptions(sensitivity="D0", mode="gpu_only")
        with pytest.raises(SafeError, match="alias_bad_reply"):
            await d.page(p, o)
        with pytest.raises(SafeError, match="gpu_budget_exhausted"):
            await d.page(p, o)
        assert len(gpu.calls) == 1 and not cpu.calls
        gpu.ready = False
        r = await d.page(p, OCROptions(sensitivity="D0"))
        assert r.source == "cpu_ocr"
    finally:
        await d.close()


async def test_queue_capacity_and_cancel(png):
    cpu = Fake(delay=0.1)
    d = Dispatcher(cpu, None, Settings(queue_size=1))
    d.start()
    try:
        p = RasterPage(0, png, 200, 120, "id")
        o = OCROptions()
        one = asyncio.create_task(d.cpu.submit(p, o))
        await asyncio.sleep(0.02)
        two = asyncio.create_task(d.cpu.submit(p, o))
        await asyncio.sleep(0.01)
        with pytest.raises(SafeError, match="queue_full"):
            await d.cpu.submit(p, o)
        two.cancel()
        await asyncio.gather(two, return_exceptions=True)
        await one
        await asyncio.sleep(0.01)
        assert len(cpu.calls) == 1 and d.cpu.active == 0
    finally:
        await d.close()


async def test_empty_reply_one_attempt_and_no_false_success(png):
    class Empty(Fake):
        async def recognize(self, page, options):
            result = await super().recognize(page, options)
            result.text = ""
            result.markdown = ""
            return result

    d = Dispatcher(Empty(), None, Settings())
    d.start()
    try:
        with pytest.raises(SafeError, match="empty_nonblank") as e:
            await d.page(
                RasterPage(0, png, 200, 120, "id", features={"blank": False}),
                OCROptions(mode="cpu_only"),
            )
        assert len(e.value.attempts) == 1 and d.cpu.completed == 0
    finally:
        await d.close()


async def test_gpu_only_not_blocked_by_cpu_health(png):
    class Slow(Fake):
        async def health(self):
            await asyncio.sleep(10)
            return await super().health()

    d = Dispatcher(Slow(), Fake("gpu"), Settings(gpu_enabled=True))
    d.start()
    try:
        result = await asyncio.wait_for(
            d.page(
                RasterPage(0, png, 200, 120, "id"), OCROptions(mode="gpu_only", sensitivity="D0")
            ),
            0.5,
        )
        assert result.source == "alias_ocr"
    finally:
        await d.close()
