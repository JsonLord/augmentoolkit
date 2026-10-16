"""Real CPU benchmark. Mocked provider timings never qualify overflow."""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import resource
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import psutil

from .adapters.rapidocr_cpu import RapidCPU
from .config import Settings
from .fixtures.generate import generate
from .ingest import ingest
from .routing import Dispatcher
from .schemas import OCROptions, SafeError


def distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        row = [i]
        for j, y in enumerate(b, 1):
            row.append(min(row[-1] + 1, prev[j] + 1, prev[j - 1] + (x != y)))
        prev = row
    return prev[-1]


async def benchmark(args):
    manifest = generate(args.fixtures)
    settings = Settings.from_env()
    cpu = RapidCPU(settings.models_dir)
    process = psutil.Process()
    cpu_start = time.process_time()
    init = time.perf_counter()
    await cpu.initialize()
    init_ms = (time.perf_counter() - init) * 1000
    dispatcher = Dispatcher(cpu, None, settings)
    dispatcher.start()
    options = OCROptions(mode="cpu_only", sensitivity="D0")
    rows = []
    start = time.perf_counter()
    try:
        for fixture in manifest:
            path = Path(args.fixtures) / fixture["file"]
            pages = ingest(path.read_bytes(), path.name, options, settings)
            times = []
            result = None
            for _ in range(args.runs):
                t = time.perf_counter()
                try:
                    result = await dispatcher.page(pages[0], options)
                except SafeError as e:
                    rows.append(
                        dict(
                            fixture=fixture["file"],
                            category=fixture["category"],
                            source="failed",
                            failure=e.code,
                            latency_ms=[(time.perf_counter() - t) * 1000],
                            cer=None,
                            wer=None,
                        )
                    )
                    break
                times.append((time.perf_counter() - t) * 1000)
            if result is None:
                continue
            reference = " ".join(fixture["text"].split())
            actual = " ".join(result.text.split())
            rows.append(
                dict(
                    fixture=fixture["file"],
                    category=fixture["category"],
                    source=result.source,
                    latency_ms=times,
                    cer=distance(reference, actual) / max(1, len(reference)),
                    wer=distance(reference.split(), actual.split())
                    / max(1, len(reference.split())),
                    lines=len(result.blocks),
                    flags=result.flags,
                    box_bounds_valid=all(
                        all(0 <= v <= 1000 for v in b.bbox_1000)
                        for b in result.blocks
                        if b.bbox_1000
                    ),
                )
            )
        batches = []
        for count in args.batch_sizes:
            path = Path(args.fixtures) / f"mixed-{count}.pdf"
            batch_start = time.perf_counter()
            pages = ingest(path.read_bytes(), path.name, options, settings)
            # Bound per-job fanout as in production, while preserving original order.
            sem = asyncio.Semaphore(settings.pages_per_job)

            async def run(p, sem=sem):
                async with sem:
                    return await dispatcher.page(p, options)

            results = await asyncio.gather(*(run(p) for p in pages))
            elapsed = time.perf_counter() - batch_start
            batches.append(
                dict(
                    pages=count,
                    seconds=elapsed,
                    pages_per_minute=count / elapsed * 60,
                    ordered=[r.page_index for r in results] == list(range(count)),
                    embedded=sum(r.source == "embedded_text" for r in results),
                    cpu=sum(r.source == "cpu_ocr" for r in results),
                    mode="cpu_only",
                    gpu_calls=0,
                )
            )
        elapsed = time.perf_counter() - start
        lat = [v for row in rows if row["source"] == "cpu_ocr" for v in row["latency_ms"]]
        root = Path(__file__).parent
        fingerprint = hashlib.sha256()
        for path in sorted(root.rglob("*.py")):
            if "tests" not in path.parts:
                fingerprint.update(str(path.relative_to(root)).encode())
                fingerprint.update(path.read_bytes())
        revision = os.getenv("OCR_SOURCE_REVISION")
        if not revision and shutil.which("git"):
            try:
                revision = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
                ).strip()
            except subprocess.CalledProcessError:
                pass
        report = dict(
            schema_version="ocr-benchmark/v1",
            commit=revision,
            runtime_sha256=fingerprint.hexdigest(),
            cpu_quota=Path("/sys/fs/cgroup/cpu.max").read_text().strip()
            if Path("/sys/fs/cgroup/cpu.max").exists()
            else None,
            memory_limit=Path("/sys/fs/cgroup/memory.max").read_text().strip()
            if Path("/sys/fs/cgroup/memory.max").exists()
            else None,
            platform=platform.platform(),
            python=platform.python_version(),
            logical_cpus=os.cpu_count(),
            engine="PP-OCRv6 Small / RapidOCR 3.10.0 / ORT CPU 1.24.1",
            model_init_ms=init_ms,
            warm_latency_ms={str(p): float(np.percentile(lat, p)) for p in (50, 95, 99)},
            failed_fixtures=sum(row["source"] == "failed" for row in rows),
            process_threads=process.num_threads(),
            peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            cpu_seconds=time.process_time() - cpu_start,
            wall_seconds=elapsed,
            rows=rows,
            batches=batches,
            limitations=[
                "Synthetic printed ground truth only; no human handwriting corpus",
                "No live GPU, quota/cost, HF Basic, OpenVINO, table semantics or ground-truth box IoU qualification",
                "No mixed CPU/GPU throughput gain measured; overflow remains disabled",
            ],
        )
        Path(args.output).write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k not in ("rows",)}, indent=2))
    finally:
        await dispatcher.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--fixtures", default="/tmp/ocr-fixtures")
    p.add_argument("--output", default="/tmp/ocr-benchmark.json")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--batch-sizes", type=int, nargs="*", default=[20, 100])
    asyncio.run(benchmark(p.parse_args()))
