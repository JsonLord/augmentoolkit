"""Explicit, checksum-verified model acquisition. Never called during inference."""

import hashlib
import json
import os
from pathlib import Path

import httpx


def download(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(__file__).with_name("models.json").read_text())
    for item in manifest.values():
        dest = directory / item["filename"]
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == item["sha256"]:
            continue
        tmp = dest.with_suffix(".part")
        digest = hashlib.sha256()
        try:
            # Fixed authoritative URLs, redirects permitted for artifact CDN only (no credentials).
            with httpx.stream("GET", item["url"], follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                with tmp.open("wb") as f:
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 150 * 1024 * 1024:
                            raise RuntimeError("model_size_limit")
                        f.write(chunk)
                        digest.update(chunk)
            if digest.hexdigest() != item["sha256"]:
                raise RuntimeError("model_checksum_mismatch")
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    download(os.getenv("OCR_MODELS_DIR", "/tmp/ocr-models"))
