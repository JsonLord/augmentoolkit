"""Synthetic combined-image qualification. Only ephemeral loopback is published.

--baseline records old refresh/admission failures without inference. Default tests
one OCR alongside browser/static/listing traffic. Never enables job execution.
"""

import argparse
import base64
import concurrent.futures
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

PAGES = [
    "/",
    "/inputs",
    "/outputs",
    "/configs",
    "/monitor",
    "/logs",
    "/chat",
    "/configs/example",
    "/logs/example",
]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def memory_cpu(name):
    # PID 1 is the sole Uvicorn process. No shell/credentials included in evidence.
    data = docker("exec", name, "cat", "/proc/1/status")
    values = {
        key: int(re.search(rf"^{key}:\s+(\d+)", data, re.MULTILINE)[1]) * 1024
        for key in ["VmRSS", "VmHWM"]
    }
    stat = docker("exec", name, "cat", "/sys/fs/cgroup/cpu.stat")
    values["cpu_usage_usec"] = int(re.search(r"usage_usec (\d+)", stat)[1])
    values["observed_at_monotonic"] = time.monotonic()
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="augmentoolkit-gate04")
    parser.add_argument("--evidence", default="/tmp/gate04-container-results.json")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    name = "atk-gate04-" + uuid.uuid4().hex[:10]
    token = "synthetic-local-" + uuid.uuid4().hex
    evidence = {
        "image": args.image,
        "baseline": args.baseline,
        "synthetic_only": True,
        "passed": False,
        "http": [],
        "resources": [],
    }
    records_lock = threading.Lock()
    sockets = []
    stop = threading.Event()
    sampler = None

    def record(method, path, response, duration, phase):
        with records_lock:
            evidence["http"].append(
                {
                    "method": method,
                    "path": path,
                    "status": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "seconds": round(duration, 4),
                    "started_monotonic": time.perf_counter() - duration,
                    "completed_monotonic": time.perf_counter(),
                    "phase": phase,
                }
            )

    fixtures = tempfile.TemporaryDirectory(prefix="atk-config-synthetic-")
    fixture_root = Path(fixtures.name)
    (fixture_root / "template.yaml").write_text(
        "title: Synthetic configuration\nvalue: original\n"
    )
    (fixture_root / "aliases.yaml").write_text(
        "path_aliases:\n  synthetic-config: .gate04-config-fixture.yaml\npipeline_order: []\n"
    )
    for fixture in fixture_root.iterdir():
        fixture.chmod(0o444)
    try:
        docker(
            "run",
            "--detach",
            "--name",
            name,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs",
            "/tmp:mode=1777",
            "--tmpfs",
            "/data:uid=1000,gid=1000,mode=0700",
            "--publish",
            "127.0.0.1::7860",
            "--env",
            "WORKER_TOKEN=" + token,
            "--env",
            "REDIS_PORT=1",
            "--mount",
            f"type=bind,src={fixture_root / 'template.yaml'},dst=/app/.gate04-config-fixture.yaml,readonly",
            "--mount",
            f"type=bind,src={fixture_root / 'aliases.yaml'},dst=/app/super_config.yaml,readonly",
            args.image,
        )
        evidence["image_id"] = docker("inspect", "--format", "{{.Image}}", name)
        evidence["command"] = json.loads(
            docker("inspect", "--format", "{{json .Config.Cmd}}", name)
        )
        binding = json.loads(
            docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", name)
        )["7860/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        origin = "http://127.0.0.1:" + binding["HostPort"]
        headers = {"Authorization": "Bearer " + token}
        with httpx.Client(base_url=origin, trust_env=False, timeout=45) as client:

            def req(method, path, phase="boundary", **kw):
                t = time.perf_counter()
                r = client.request(method, path, **kw)
                record(method, path, r, time.perf_counter() - t, phase)
                return r

            for _ in range(120):
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                raise AssertionError("Container startup timeout")
            assert docker("exec", name, "id", "-u") == "1000"
            evidence["resources"].append(memory_cpu(name))
            for path in PAGES:
                r = req("GET", path, "direct_refresh")
                if not args.baseline:
                    assert (
                        r.status_code == 200
                        and "text/html" in r.headers["content-type"]
                    ), path
            # Deterministic reproduction: ordinary idle HTTP/1.1 clients count
            # toward Uvicorn's connection admission budget, even without OCR.
            for _ in range(8):
                sockets.append(
                    socket.create_connection(
                        ("127.0.0.1", int(binding["HostPort"])), timeout=5
                    )
                )
            time.sleep(0.1)
            r = req("GET", "/health", "idle_connection_admission")
            evidence["eight_idle_connections_status"] = r.status_code
            for connection in sockets:
                connection.close()
            sockets.clear()
            if args.baseline:
                assert r.status_code == 503
                assert any(v["status"] == 404 for v in evidence["http"])
                evidence["old_failures_reproduced"] = True
                evidence["passed"] = True
                return
            assert r.status_code == 200
            assert req("GET", "/ocr/health").status_code == 200
            assert req("GET", "/ocr/ready", headers=headers).json() == {"ready": True}
            for mode in [None, "invalid"]:
                h = {} if mode is None else {"Authorization": "Bearer invalid"}
                for path in [
                    "/inputs/structure/",
                    "/tasks/queue",
                    "/jobs",
                    "/ocr/ready",
                ]:
                    assert req("GET", path, headers=h).status_code == 401
            for path in ["/tasks/unknown", "/api/unknown", "/assets/missing.js"]:
                r = req("GET", path)
                assert (
                    r.status_code == 404
                    and "application/json" in r.headers["content-type"]
                )
            for method, path, kw in [
                ("GET", "/tasks/queue", {}),
                ("POST", "/pipelines/run", {"json": {"node_path": "never-execute"}}),
            ]:
                assert (
                    req(
                        method, path, "intentional_queue_503", headers=headers, **kw
                    ).status_code
                    == 503
                )
            evidence["non_root_read_only_startup"] = True
            evidence["mounted_ocr_ready"] = True
            evidence["queue_execution_disabled"] = True
            image = Image.new("RGB", (1000, 160), "white")
            ImageDraw.Draw(image).text(
                (30, 40),
                "Public OCR benchmark.",
                font=ImageFont.truetype("DejaVuSans.ttf", 48),
                fill="black",
            )
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            png = buffer.getvalue()
            for classification in [None, "D1", "D2"]:
                for endpoint in [
                    "/internal/ocr/page",
                    "/v1/ocr/page",
                    "/v1/chat/completions",
                ]:
                    if endpoint.endswith("completions"):
                        body = {
                            "model": "ocr-cpu",
                            "messages": [
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": "data:image/png;base64,"
                                                + base64.b64encode(png).decode()
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                        if classification:
                            body["ocr_options"] = {"sensitivity": classification}
                        r = req("POST", "/ocr" + endpoint, headers=headers, json=body)
                    else:
                        r = req(
                            "POST",
                            "/ocr" + endpoint,
                            headers=headers,
                            files={"file": ("synthetic.png", png)},
                            data={}
                            if classification is None
                            else {"sensitivity": classification},
                        )
                    assert r.status_code == 422
            evidence["nine_classification_rejections"] = True

        def sample():
            while not stop.is_set():
                try:
                    evidence["resources"].append(memory_cpu(name))
                except subprocess.CalledProcessError:
                    break
                stop.wait(0.5)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        start_gate = threading.Event()
        ready_gate = threading.Barrier(12)

        def get_once(path, authenticated=False):
            start_gate.wait(10)
            with httpx.Client(base_url=origin, trust_env=False, timeout=45) as c:
                ready_gate.wait(timeout=30)
                t = time.perf_counter()
                r = c.get(path, headers=headers if authenticated else {})
                record("GET", path, r, time.perf_counter() - t, "concurrent_workload")
                assert r.status_code == 200, (path, r.status_code)
                return r

        def infer():
            start_gate.wait(10)
            with httpx.Client(base_url=origin, trust_env=False, timeout=45) as c:
                ready_gate.wait(timeout=30)
                t = time.perf_counter()
                r = c.post(
                    "/ocr/v1/ocr/page",
                    headers=headers,
                    files={"file": ("synthetic.png", png)},
                    data={"sensitivity": "D0"},
                )
                record(
                    "POST",
                    "/ocr/v1/ocr/page",
                    r,
                    time.perf_counter() - t,
                    "concurrent_workload",
                )
                assert r.status_code == 200
                result = r.json()
                assert " ".join(result["text"].split()) == "Public OCR benchmark."
                assert result["sensitivity"] == "D0" and result["source"] == "cpu_ocr"
                assert (
                    result["engine"] == "pp-ocrv6-small"
                    and result["provider_destination"] == "local-cpu"
                )
                evidence["synthetic_d0"] = {
                    "seconds": round(time.perf_counter() - t, 4),
                    "result": result,
                }

        with (
            sync_playwright() as p,
            concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool,
        ):
            browser = p.chromium.launch(
                executable_path=os.environ.get("CHROMIUM_EXECUTABLE")
                or shutil.which("chromium"),
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(type(error).__name__))

                def browser_response(r):
                    path = urlsplit(r.url).path
                    content_type = r.headers.get("content-type", "")
                    phase = "browser"
                    bearer_present = "authorization" in r.request.headers
                    if (
                        r.status == 401
                        and not bearer_present
                        and "application/json" in content_type
                    ):
                        assert r.json() == {"detail": "Unauthorized"}
                        phase = "browser_locked_401"
                    # Classify only documented, JSON backend failures; a plain
                    # Uvicorn admission 503 must still fail the workload.
                    if (
                        r.status == 404
                        and path.startswith("/configs/content/")
                        and "application/json" in content_type
                    ):
                        phase = "browser_unqualified_legacy"
                    elif (
                        r.status == 503
                        and path in {"/tasks/queue", "/tasks/example/logs"}
                        and "application/json" in content_type
                        and r.json() == {"detail": "Huey/Redis unavailable"}
                    ):
                        phase = "browser_disabled_queue_503"
                    evidence["http"].append(
                        {
                            "method": r.request.method,
                            "path": path,
                            "status": r.status,
                            "content_type": content_type,
                            "application_bearer_present": bearer_present,
                            "phase": phase,
                        }
                    )

                page.on("response", browser_response)
                page.goto(origin, wait_until="networkidle")
                asset = page.locator('script[type="module"]').get_attribute("src")
                assert asset.startswith("/assets/")
                # Five authenticated listing clients + six static clients +
                # exactly one real OCR request overlap browser reload/navigation.
                futures = [
                    pool.submit(get_once, path, True)
                    for path in [
                        "/inputs/structure/",
                        "/outputs/structure/",
                        "/configs/structure/",
                        "/jobs",
                        "/pipelines/available",
                    ]
                ]
                futures += [pool.submit(get_once, asset) for _ in range(6)]
                futures += [pool.submit(infer)]
                start_gate.set()
                for _ in range(10):
                    r = page.reload(wait_until="networkidle")
                    assert r.status == 200
                    page.get_by_label("Operator token").wait_for()
                evidence["browser_reloads"] = 10
                page.get_by_label("Operator token").fill(token)
                page.get_by_role("button", name="Unlock operator access").click()
                page.get_by_role("button", name="Lock operator access").wait_for()
                # Inputs listing verified explicitly, not just rendering the page.
                with page.expect_response(
                    lambda r: "/inputs/structure/" in r.url and r.status == 200
                ):
                    page.get_by_role("link", name="Inputs", exact=True).click()
                for i in range(20):
                    page.get_by_role(
                        "link",
                        name=["Inputs", "Outputs", "Configs", "Chat"][i % 4],
                        exact=True,
                    ).click()
                    page.wait_for_timeout(100)
                evidence["route_navigations"] = 20
                page.get_by_role("link", name="Inputs", exact=True).click()
                page.get_by_role("button", name="Lock operator access").click()
                page.get_by_label("Operator token").wait_for()
                for path in PAGES:
                    r = page.goto(origin + path, wait_until="networkidle")
                    assert r.status == 200 and "text/html" in r.headers["content-type"]
                    r = page.reload(wait_until="networkidle")
                    assert r.status == 200
                    page.get_by_label("Operator token").wait_for()
                for future in futures:
                    future.result(timeout=60)
                from file_workflows import config_workflow, input_workflow

                evidence["input_manager"] = input_workflow(page, origin, token)
                evidence["config_editor"] = config_workflow(page, origin, token)
                assert not errors, errors
                evidence["browser"] = {
                    "direct_refresh_routes": PAGES,
                    "unlock_inputs_lock_refresh_locked": True,
                    "javascript_errors": errors,
                }
            finally:
                browser.close()
        listings = [
            r
            for r in evidence["http"]
            if r["phase"] == "concurrent_workload"
            and r["method"] == "GET"
            and not r["path"].startswith("/assets/")
        ]
        assert len(listings) == 5
        evidence["five_listing_intervals_overlap"] = max(
            r["started_monotonic"] for r in listings
        ) < min(r["completed_monotonic"] for r in listings)
        assert evidence["five_listing_intervals_overlap"]
        evidence["resources"].append(memory_cpu(name))
        before, after = evidence["resources"][0], evidence["resources"][-1]
        evidence["steady_state_rss_growth_bytes"] = after["VmRSS"] - before["VmRSS"]
        assert evidence["steady_state_rss_growth_bytes"] < 128 * 1024 * 1024
        evidence["cpu_average_cores"] = round(
            (after["cpu_usage_usec"] - before["cpu_usage_usec"])
            / 1e6
            / (after["observed_at_monotonic"] - before["observed_at_monotonic"]),
            3,
        )
        stop.set()
        sampler.join(timeout=5)
        process_lines = docker("top", name, "-eo", "pid,args").splitlines()[1:]
        assert len(process_lines) == 1 and "uvicorn" in process_lines[0]
        evidence["one_server_process_no_job_worker"] = True
        assert evidence["command"][evidence["command"].index("--workers") + 1] == "1"
        assert (
            evidence["command"][evidence["command"].index("--limit-concurrency") + 1]
            == "32"
        )
        unintended = [
            r
            for r in evidence["http"]
            if r["phase"]
            in [
                "browser",
                "concurrent_workload",
                "direct_refresh",
                "idle_connection_admission",
            ]
            and r["status"] >= 400
        ]
        assert not unintended, unintended
        evidence["passed"] = True
    except Exception:
        # Only synthetic paths/statuses; never headers, tokens or file contents.
        print(json.dumps({"failure_recent_http": evidence["http"][-15:]}), flush=True)
        raise
    finally:
        stop.set()
        if sampler:
            sampler.join(timeout=5)
        for connection in sockets:
            connection.close()
        logs = subprocess.run(
            ["docker", "logs", name], check=False, capture_output=True, text=True
        ).stderr
        # Constant-only log evidence; no raw URLs, headers, credentials or payloads.
        evidence["sanitized_server_logs"] = {
            key: logs.count(message)
            for key, message in {
                "concurrency_limit_warnings": "Exceeded concurrency limit",
                "server_process_starts": "Started server process",
                "startup_complete": "Application startup complete",
                "tracebacks": "Traceback (most recent call last)",
            }.items()
        }
        if (
            not args.baseline
            and evidence["sanitized_server_logs"]["concurrency_limit_warnings"]
        ):
            evidence["passed"] = False
        Path(args.evidence).write_text(json.dumps(evidence, indent=2) + "\n")
        subprocess.run(
            ["docker", "rm", "--force", name], check=False, capture_output=True
        )
        fixtures.cleanup()
    assert evidence["passed"], evidence["sanitized_server_logs"]
    print(json.dumps({"passed": True, "evidence": args.evidence}))


if __name__ == "__main__":
    main()
