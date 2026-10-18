"""Bounded offline runtime qualification, never a service or production launcher."""

import argparse
import hashlib
import json
import multiprocessing
import resource
import sys
import time
import traceback
from pathlib import Path


def pressure(ready, stop):
    allocation = bytearray(1536 * 1024 * 1024)
    for i in range(0, len(allocation), 4096):
        allocation[i] = 1
    ready.set()
    value = 0
    while not stop.is_set():
        value = (value * 1664525 + 1013904223) % (2**32)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--hybrid", action="store_true", help="Measure selected model + terminology path"
    )
    parser.add_argument(
        "--paired", action="store_true", help="Alternate same-text model/control timings"
    )
    args = parser.parse_args()
    if args.paired and not args.hybrid:
        parser.error("paired measurement requires hybrid")
    denied = []

    def audit(event, values):
        if event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
            denied.append(
                dict(
                    event=event, callsite=[frame.name for frame in traceback.extract_stack(limit=5)]
                )
            )
            raise PermissionError("staging_network_disabled")

    sys.addaudithook(audit)
    # The Docker network namespace is additionally network=none, with no ports.
    context = multiprocessing.get_context("spawn")
    ready, stop = context.Event(), context.Event()
    load = context.Process(target=pressure, args=(ready, stop))
    load.start()
    try:
        if not ready.wait(30):
            raise RuntimeError("pressure_not_ready")
        from .atomic_output import write_candidate
        from .model import verify_cache
        from .recognizer import GLiNERRecognizer

        start = time.perf_counter()
        verify_cache(args.cache)
        integrity_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        model = GLiNERRecognizer(args.cache)
        loading_ms = (time.perf_counter() - start) * 1000
        from .engine import AnonymizationEngine

        engine = AnonymizationEngine(model) if args.hybrid else None
        samples, baseline_samples = [], []
        for iteration in range(8):
            start = time.perf_counter()
            text = "Synthetic reviewer Petra Ashcroft works at Harbor Research in Norwich."
            if engine is None:
                entities = model.recognize(text)
            else:
                _, entities, _ = engine.transform(text + " Religious status: agnostic.")
            if not entities:
                raise RuntimeError("model_not_ready")
            if args.paired:
                # Both arms use the identical independent synthetic sentence.
                # Alternate order to expose warmup/order effects, not hide them.
                from .detectors import detect_contacts
                from .replacement import replace

                paired_text = text + " Religious status: agnostic."
                order = ("control", "hybrid") if iteration % 2 else ("hybrid", "control")
                for arm in order:
                    start = time.perf_counter()
                    if arm == "control":
                        replace(
                            paired_text, model.recognize(paired_text) + detect_contacts(paired_text)
                        )
                    else:
                        engine.transform(paired_text)
                    duration = (time.perf_counter() - start) * 1000
                    (samples if arm == "hybrid" else baseline_samples).append(duration)
            else:
                samples.append((time.perf_counter() - start) * 1000)
        limit = int(Path("/sys/fs/cgroup/memory.max").read_text())
        current = int(Path("/sys/fs/cgroup/memory.current").read_text())
        peak = int(Path("/sys/fs/cgroup/memory.peak").read_text())
        listening = 0
        for table in ("tcp", "tcp6"):
            for line in Path("/proc/net/" + table).read_text().splitlines()[1:]:
                listening += line.split()[3] == "0A"
        result = dict(
            probe="single-worker-with-1536MiB-concurrent-CPU-memory-load",
            recognition_path="hybrid-terminology" if args.hybrid else "model-only",
            integrity_ms=integrity_ms,
            loading_ms=loading_ms,
            warm_ms=samples,
            same_text_control_ms=baseline_samples,
            process_peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            cgroup_memory_limit_mib=limit / 2**20,
            cgroup_current_mib=current / 2**20,
            cgroup_peak_mib=peak / 2**20,
            measured_headroom_mib=(limit - peak) / 2**20,
            cpu_quota=Path("/sys/fs/cgroup/cpu.max").read_text().strip(),
            denied_network_calls=denied,
            native_tcp_listeners_at_measurement=listening,
            source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            automated_release_eligible=False,
        )
        write_candidate(args.output, (json.dumps(result, indent=2) + "\n").encode())
    finally:
        stop.set()
        load.join(10)
        if load.is_alive():
            load.kill()
            load.join()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("private_staging_probe_failed") from None
