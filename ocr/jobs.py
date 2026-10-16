import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field

from .normalize import markdown_pages
from .parse_process import parse
from .schemas import Failure, OCRResult, SafeError


@dataclass
class Job:
    id: str
    owner: str
    request_id: str
    sensitivity: str
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    status: str = "queued"
    page_count: int = 0
    completed_pages: int = 0
    failures: list = field(default_factory=list)
    result: OCRResult | None = None
    task: asyncio.Task | None = None
    fingerprint: str = ""
    stored_bytes: int = 0


class Jobs:
    def __init__(self, dispatcher, settings):
        self.dispatcher = dispatcher
        self.settings = settings
        self.jobs = {}
        self.keys = {}
        self.cleaner = None
        self.parse_slots = asyncio.Semaphore(1)

    def start(self):
        self.cleaner = asyncio.create_task(self._cleanup())

    async def close(self):
        if self.cleaner:
            self.cleaner.cancel()
        tasks = [j.task for j in self.jobs.values() if j.task and not j.task.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.cleaner:
            await asyncio.gather(self.cleaner, return_exceptions=True)
        self.jobs.clear()
        self.keys.clear()

    def purge_expired(self):
        now = time.time()
        for id, j in list(self.jobs.items()):
            if now - j.created > self.settings.ttl:
                if j.task and not j.task.done():
                    j.task.cancel()
                del self.jobs[id]
                for key, value in list(self.keys.items()):
                    if value == id:
                        del self.keys[key]

    async def _cleanup(self):
        while True:
            await asyncio.sleep(min(30, self.settings.ttl))
            self.purge_expired()

    def get(self, id, owner):
        self.purge_expired()
        j = self.jobs.get(id)
        if not j or j.owner != owner:
            raise SafeError("job_not_found")
        return j

    def submit(self, data, filename, options, owner, key=None):
        self.purge_expired()
        fingerprint = hashlib.sha256(data + options.model_dump_json().encode()).hexdigest()
        if key and (owner, key) in self.keys:
            j = self.get(self.keys[(owner, key)], owner)
            if j.fingerprint != fingerprint:
                raise SafeError("idempotency_conflict")
            return j
        if (
            len(self.jobs) >= self.settings.retained_jobs
            or sum(j.status in ("queued", "running") for j in self.jobs.values())
            >= self.settings.active_jobs
        ):
            raise SafeError("job_capacity")
        j = Job(
            str(uuid.uuid4()),
            owner,
            str(uuid.uuid4()),
            options.sensitivity,
            fingerprint=fingerprint,
        )
        self.jobs[j.id] = j
        if key:
            self.keys[(owner, key)] = j.id
        j.task = asyncio.create_task(self._run(j, data, filename, options))
        return j

    async def parse(self, data, filename, options):
        async with self.parse_slots:
            return await parse(data, filename, options, self.settings)

    async def _run(self, j, data, filename, options):
        results = []
        tasks = []
        try:
            async with asyncio.timeout(self.settings.job_timeout):
                pages = await self.parse(data, filename, options)
                data = b""
                j.page_count = len(pages)
                j.status = "running"
                slots = asyncio.Semaphore(self.settings.pages_per_job)

                async def one(page):
                    async with slots:
                        try:
                            result = await self.dispatcher.page(page, options)
                            current = sum(len(p.text) + len(p.markdown) for p in results)
                            if (
                                current + len(result.model_dump_json())
                                > self.settings.max_result_bytes
                            ):
                                raise SafeError("result_limit")
                            results.append(result)
                        except SafeError as e:
                            j.failures.append(Failure(page_index=page.index, code=e.code))
                        except Exception:
                            j.failures.append(Failure(page_index=page.index, code="page_failed"))
                        finally:
                            j.completed_pages += 1
                            j.updated = time.time()

                tasks = [asyncio.create_task(one(p)) for p in pages]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    pages.clear()
                j.status = (
                    "partial" if results and j.failures else "failed" if j.failures else "completed"
                )
                results.sort(key=lambda p: p.page_index)
                j.result = OCRResult(
                    job_id=j.id,
                    request_id=j.request_id,
                    status=j.status,
                    page_count=j.page_count,
                    sensitivity=j.sensitivity,
                    markdown=markdown_pages(results)
                    if options.output in ("markdown", "both")
                    else "",
                    text="\n\n".join(p.text for p in results)
                    if options.output in ("text", "both")
                    else "",
                    pages=results,
                    failures=sorted(j.failures, key=lambda p: p.page_index),
                )
                size = len(j.result.model_dump_json().encode("utf-8"))
                if (
                    size > self.settings.max_result_bytes
                    or size + sum(other.stored_bytes for other in self.jobs.values())
                    > self.settings.retained_bytes
                ):
                    j.result = None
                    raise SafeError("result_capacity")
                j.stored_bytes = size
        except asyncio.CancelledError:
            j.status = "cancelled"
            j.result = None
            j.failures = []
            raise
        except Exception as e:
            j.status = "failed"
            j.failures = [
                Failure(
                    page_index=-1,
                    code=e.code
                    if isinstance(e, SafeError)
                    else "job_timeout"
                    if isinstance(e, TimeoutError)
                    else "job_failed",
                )
            ]
        finally:
            data = b""
            j.updated = time.time()

    def cancel(self, j):
        if j.task and not j.task.done():
            j.task.cancel()
        j.status = "cancelled"
        j.stored_bytes = 0
        j.result = None
        j.failures = []
        for key, id in list(self.keys.items()):
            if id == j.id:
                del self.keys[key]


def status(j):
    return dict(
        job_id=j.id,
        request_id=j.request_id,
        status=j.status,
        page_count=j.page_count,
        completed_pages=j.completed_pages,
        created=j.created,
        updated=j.updated,
        failures=[f.model_dump() for f in j.failures],
    )
