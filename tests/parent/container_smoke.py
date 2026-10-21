"""Run against a newly built local image; synthetic input, no job submission succeeds.

Usage: python tests/parent/container_smoke.py [--image augmentoolkit-gate04]
Requires Docker, httpx, Pillow, Playwright and Chromium. Publishes only loopback
on an ephemeral port, removes its own container even on failure.
"""

import argparse
import base64
import io
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="augmentoolkit-gate04")
    parser.add_argument("--evidence", default="/tmp/gate04-container-results.json")
    args = parser.parse_args()
    name = "atk-gate04-" + uuid.uuid4().hex[:10]
    token = "synthetic-local-test-" + uuid.uuid4().hex
    evidence = {"image": args.image, "synthetic_only": True, "passed": False}
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
            "--env",
            "HTTP_PROXY=",
            "--env",
            "HTTPS_PROXY=",
            "--env",
            "ALL_PROXY=",
            args.image,
        )
        binding = json.loads(
            docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", name)
        )["7860/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        origin = "http://127.0.0.1:" + binding["HostPort"]
        headers = {"Authorization": "Bearer " + token}
        with httpx.Client(base_url=origin, trust_env=False, timeout=30) as client:
            for attempt in range(120):
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                raise AssertionError("Container failed to start")
            assert docker("exec", name, "id", "-u") == "1000"
            assert client.get("/ocr/health").status_code == 200
            assert client.get("/ocr/ready", headers=headers).json() == {"ready": True}
            assert client.get("/ocr/docs").status_code == 200
            assert client.get("/ocr/ready").status_code == 401
            assert client.get("/tasks/synthetic/logs").status_code == 401
            assert client.get("/jobs").status_code == 401
            assert (
                client.get("/pipelines/available", headers=headers).status_code == 200
            )
            assert client.get("/tasks/queue", headers=headers).status_code == 503
            response = client.post(
                "/pipelines/run", headers=headers, json={"node_path": "never-execute"}
            )
            assert response.status_code == 503
            evidence["non_root_read_only_startup"] = True
            evidence["mounted_ocr_ready"] = True
            evidence["redis_unavailable_fail_closed"] = True
            # Exactly one synthetic D0 recognition. Rejections below never infer.
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
            start = time.perf_counter()
            response = client.post(
                "/ocr/v1/ocr/page",
                headers=headers,
                files={"file": ("synthetic-d0.png", png)},
                data={"sensitivity": "D0"},
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert "Public OCR benchmark." == " ".join(result["text"].split()), result
            assert result["sensitivity"] == "D0", result
            assert (
                result["engine"] == "pp-ocrv6-small" and result["source"] == "cpu_ocr"
            )
            evidence["synthetic_d0"] = {
                "seconds": round(time.perf_counter() - start, 3),
                "result": result,
            }
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
                        response = client.post(
                            "/ocr" + endpoint, headers=headers, json=body
                        )
                    else:
                        response = client.post(
                            "/ocr" + endpoint,
                            headers=headers,
                            files={"file": ("synthetic.png", png)},
                            data={}
                            if classification is None
                            else {"sensitivity": classification},
                        )
                    assert response.status_code == 422, (
                        endpoint,
                        classification,
                        response.text,
                    )
            evidence["nine_sensitive_or_missing_class_rejections"] = True
        with sync_playwright() as p:
            executable = os.environ.get("CHROMIUM_EXECUTABLE") or shutil.which(
                "chromium"
            )
            browser = p.chromium.launch(
                executable_path=executable,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page()
            failures = []
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.goto(origin, wait_until="networkidle")
            page.get_by_label("Operator token").fill(token)
            page.get_by_role("button", name="Unlock operator access").click()
            page.get_by_role("button", name="Lock operator access").wait_for()
            # Exercise the authenticated frontend against the combined server.
            with page.expect_response(
                lambda r: "/inputs/structure/" in r.url and r.status == 200
            ):
                page.get_by_role("link", name="Inputs", exact=True).click()
            assert not failures, failures
            page.get_by_role("button", name="Lock operator access").click()
            page.get_by_label("Operator token").wait_for()
            browser.close()
            evidence["browser"] = {
                "unlock_inputs_lock": True,
                "javascript_errors": failures,
            }
        processes = docker("top", name, "-eo", "pid,args")
        assert (
            "huey_consumer" not in processes and "run_augmentoolkit.py" not in processes
        )
        evidence["only_web_server_no_job_worker"] = True
        evidence["passed"] = True
    finally:
        Path(args.evidence).write_text(json.dumps(evidence, indent=2) + "\n")
        subprocess.run(
            ["docker", "rm", "--force", name], check=False, capture_output=True
        )
    print(json.dumps({"passed": True, "evidence": args.evidence}))


if __name__ == "__main__":
    main()
