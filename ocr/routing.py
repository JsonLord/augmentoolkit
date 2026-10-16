"""Independent bounded lanes with retry-time policy checks and warm EWMAs."""

import asyncio
import importlib.metadata
import random
import time
from collections import deque
from dataclasses import dataclass

from .quality import quality
from .schemas import Attempt, SafeError
from .security import authorize


@dataclass
class Work:
    page: object
    options: object
    future: asyncio.Future
    queued: float


class Lane:
    def __init__(self, backend, workers, settings):
        self.backend = backend
        self.settings = settings
        self.workers = workers
        self.queue = asyncio.Queue(settings.queue_size)
        self.tasks = []
        self.active = 0
        self.ewma = None
        self.samples = deque(maxlen=500)
        self.failures = 0
        self.open_until = 0
        self.calls = deque()
        self.completed = 0
        self.errors = 0
        self.cold_ms = None

    def start(self):
        self.tasks = [asyncio.create_task(self._worker()) for _ in range(self.workers)]

    async def close(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        while not self.queue.empty():
            item = self.queue.get_nowait()
            if not item.future.done():
                item.future.cancel()
            self.queue.task_done()
        close = getattr(self.backend, "close", None)
        if close:
            await close()

    async def eligible(self, options):
        if time.monotonic() < self.open_until:
            return False
        try:
            authorize(self.backend, options, self.settings, await self.backend.capabilities())
            return (
                await asyncio.wait_for(self.backend.health(), min(5, self.settings.timeout))
            ).ready
        except (SafeError, asyncio.TimeoutError):
            return False

    def estimate(self):
        return (
            (self.queue.qsize() + self.active + self.workers) / self.workers * (self.ewma or 5000)
        )

    async def submit(self, page, options):
        f = asyncio.get_running_loop().create_future()
        try:
            self.queue.put_nowait(Work(page, options, f, time.monotonic()))
        except asyncio.QueueFull:
            raise SafeError("queue_full", True) from None
        try:
            return await f
        except asyncio.CancelledError:
            f.cancel()
            raise

    async def _worker(self):
        while True:
            w = await self.queue.get()
            counted = False
            try:
                if w.future.cancelled():
                    continue
                self.active += 1
                counted = True
                try:
                    value = await self._execute(w)
                    if not w.future.done():
                        w.future.set_result(value)
                except SafeError as e:
                    if not w.future.done():
                        w.future.set_exception(e)
            finally:
                if counted:
                    self.active = max(0, self.active - 1)
                self.queue.task_done()

    async def _execute(self, w):
        attempts = []
        for retry in range(2):
            if w.future.cancelled():
                raise SafeError("cancelled")
            # Policy and health are re-evaluated immediately before EVERY request.
            authorize(
                self.backend,
                w.options,
                self.settings,
                await self.backend.capabilities(),
            )
            if time.monotonic() < self.open_until:
                raise SafeError("circuit_open")
            if not (await self.backend.health()).ready:
                raise SafeError("backend_unavailable", True)
            if self.backend.name == "gpu":
                now = time.monotonic()
                while self.calls and now - self.calls[0] > 60:
                    self.calls.popleft()
                if len(self.calls) >= self.settings.gpu_budget:
                    raise SafeError("gpu_budget_exhausted")
                self.calls.append(now)
            start = time.monotonic()
            wait = (start - w.queued) * 1000
            try:
                result = await asyncio.wait_for(
                    self.backend.recognize(w.page, w.options), self.settings.timeout
                )
                result.flags = quality(result, w.page)
                if "empty_nonblank" in result.flags:
                    raise SafeError("empty_nonblank")
                ms = (time.monotonic() - start) * 1000
                attempts.append(
                    Attempt(
                        backend=self.backend.name,
                        destination=result.provider_destination,
                        engine=result.engine,
                        outcome="ok",
                        latency_ms=ms,
                        queue_wait_ms=wait,
                    )
                )
                self.failures = 0
                self.completed += 1
                # Exclude first successful request (cold) from warm capacity estimates.
                if self.cold_ms is None:
                    self.cold_ms = ms
                else:
                    self.samples.append(ms)
                    self.ewma = ms if self.ewma is None else 0.2 * ms + 0.8 * self.ewma
                result.attempts = attempts
                result.attempt_count = len(attempts)
                result.latency_ms = sum(a.latency_ms for a in attempts)
                return result
            except Exception as e:
                safe = (
                    e
                    if isinstance(e, SafeError)
                    else SafeError(
                        "backend_timeout"
                        if isinstance(e, asyncio.TimeoutError)
                        else "backend_failure",
                        isinstance(e, asyncio.TimeoutError),
                    )
                )
                self.failures += 1
                self.errors += 1
                attempts.append(
                    Attempt(
                        backend=self.backend.name,
                        outcome=safe.code,
                        latency_ms=(time.monotonic() - start) * 1000,
                        queue_wait_ms=wait,
                    )
                )
                if self.failures >= 3:
                    self.open_until = time.monotonic() + 30
                if retry == 0 and safe.retryable and self.failures < 3:
                    await asyncio.sleep(0.1 + random.random() * 0.1)
                    continue
                safe.attempts = attempts
                raise safe from None


class Dispatcher:
    def __init__(self, cpu, gpu, settings):
        self.settings = settings
        self.cpu = Lane(cpu, settings.cpu_workers, settings)
        self.gpu = Lane(gpu, settings.gpu_workers, settings) if gpu else None

    def start(self):
        self.cpu.start()
        if self.gpu:
            self.gpu.start()

    async def close(self):
        await self.cpu.close()
        if self.gpu:
            await self.gpu.close()

    async def page(self, page, options):
        from .schemas import OCRPageResult

        if page.embedded and not options.force_ocr:
            return OCRPageResult(
                page_index=page.index,
                page_id=page.page_id,
                source="embedded_text",
                engine="pypdf",
                backend_version=importlib.metadata.version("pypdf"),
                provider_destination="local",
                text=page.embedded,
                markdown=page.embedded,
                image_width=page.width,
                image_height=page.height,
                rotation=page.rotation,
                route_reason="usable_pdf_text_layer",
                sensitivity=options.sensitivity,
                flags=["text_layer_heuristic"],
            )
        mode = options.mode
        if mode == "cpu_only":
            cpu_ok, gpu_ok = await self.cpu.eligible(options), False
        elif mode == "gpu_only":
            cpu_ok, gpu_ok = False, bool(self.gpu and await self.gpu.eligible(options))
        else:

            async def gpu_eligible():
                return bool(self.gpu and await self.gpu.eligible(options))

            cpu_ok, gpu_ok = await asyncio.gather(self.cpu.eligible(options), gpu_eligible())
        reason = "cpu_default"
        if mode == "cpu_only":
            lane = self.cpu if cpu_ok else None
            reason = "explicit_cpu"
        elif mode == "gpu_only":
            lane = self.gpu if gpu_ok else None
            reason = "explicit_gpu"
        else:
            lane = self.cpu if cpu_ok else self.gpu if gpu_ok else None
            if gpu_ok and page.features.get("complex") and mode in ("auto", "quality"):
                caps = await self.gpu.backend.capabilities()
                if caps.layout_blocks:
                    lane = self.gpu
                    reason = "complex_layout_verified"
            if (
                mode == "throughput"
                and gpu_ok
                and cpu_ok
                and options.allow_gpu_overflow
                and self.settings.overflow_enabled
                and self.settings.overflow_qualified
            ):
                # No speculative speedup: both warm measured capacities are mandatory.
                if (
                    self.cpu.ewma
                    and self.gpu.ewma
                    and self.gpu.estimate() < self.cpu.estimate() * 0.85
                ):
                    lane = self.gpu
                    reason = "measured_completion_time"
        if lane is None:
            raise SafeError("no_authorized_destination")
        other = self.gpu if lane is self.cpu else self.cpu
        failed_attempts = []
        attempted_names = {lane.backend.name}
        try:
            result = await lane.submit(page, options)
        except SafeError as e:
            failed_attempts = getattr(e, "attempts", [])
            if mode in ("cpu_only", "gpu_only") or not other or not await other.eligible(options):
                raise
            attempted_names.add(other.backend.name)
            result = await other.submit(page, options)
            lane = other
            other = self.gpu if lane is self.cpu else self.cpu
            result.flags += ["fallback_used"]
            reason = "approved_fallback"
        quality_failed = any(
            f in result.flags
            for f in (
                "low_confidence",
                "low_contrast_or_blur",
                "suspiciously_short",
                "repeated_text",
                "unresolved_noise",
            )
        )
        dual = mode == "quality" and self.settings.dual_run and page.features.get("complex")
        if (
            (quality_failed or dual)
            and mode not in ("cpu_only", "gpu_only")
            and other
            and other.backend.name not in attempted_names
            and await other.eligible(options)
        ):
            try:
                alt = await other.submit(page, options)
                if alt.text.strip() != result.text.strip():
                    result.flags += ["disagreement", "needs_review"]
                if quality_failed and len(alt.flags) < len(result.flags):
                    alt.alternatives = [result]
                    if alt.text.strip() != result.text.strip():
                        alt.flags += ["disagreement", "needs_review"]
                    result = alt
                    reason = "quality_escalation"
                else:
                    result.alternatives = [alt]
            except SafeError as exc:
                result.attempts += getattr(exc, "attempts", [])
                result.flags += ["escalation_unavailable", "needs_review"]
        elif quality_failed:
            result.flags += ["needs_review"]
        result.attempts = failed_attempts + result.attempts
        result.attempt_count = len(result.attempts) + sum(
            a.attempt_count for a in result.alternatives
        )
        if options.layout == "blocks" and not result.blocks:
            result.flags += ["layout_unavailable", "needs_review"]
        result.route_reason = reason
        result.page_id = page.page_id
        result.rotation = page.rotation
        result.sensitivity = options.sensitivity
        for alternative in result.alternatives:
            alternative.sensitivity = options.sensitivity
            alternative.page_id = page.page_id
            alternative.rotation = page.rotation
        if not options.preserve_boxes:
            for candidate in [result] + result.alternatives:
                for b in candidate.blocks:
                    b.bbox_1000 = None
                    b.polygon_1000 = None
        return result
