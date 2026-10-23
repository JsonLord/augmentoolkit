"""Prepare PR8's complete tracked tree. No upload, secret or Space mutation."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

SOURCE = "aee4f752ab64f189fa3962aed320347323bfafed"
PARENT = "d26a7807f1a46c44edb51d81d8afec081c6a78a8"


def main():
    root = Path("/workspace/pilot-phase-a")
    payload = root / "payload"
    payload.mkdir(mode=0o700, exist_ok=False)
    archive = subprocess.check_output(["git", "archive", SOURCE])
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(payload, filter="data")
    for p in payload.rglob("*"):
        if p.is_file() and (
            (p.name.startswith(".env") and p.name != ".env.example")
            or "secrets" in p.parts
        ):
            raise RuntimeError("Unexpected secret-like source path")
    readme = payload / "README.md"
    readme.write_text(readme.read_text().replace("title: agent-0", "title: augment", 1))
    docker = payload / "Dockerfile"
    docker.write_text(
        docker.read_text().replace(
            "ARG OCR_SOURCE_REVISION=unknown", "ARG OCR_SOURCE_REVISION=" + SOURCE, 1
        )
    )
    (payload / "STAGING_SOURCE.txt").write_text(
        f"GitHub source: {SOURCE}\nPR: https://github.com/JsonLord/augmentoolkit/pull/8\nTarget: Leon4gr45/augment\nSynthetic D0 only; jobs/providers/release disabled.\n"
    )
    manifest = {
        "source_sha": SOURCE,
        "target": "Leon4gr45/augment",
        "expected_HF_parent": PARENT,
        "status": "PREPARED_NOT_UPLOADED",
        "overlays": ["README Space title", "Docker source pin", "STAGING_SOURCE.txt"],
        "files": {
            str(p.relative_to(payload)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(payload.rglob("*"))
            if p.is_file()
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {"source": SOURCE, "files": len(manifest["files"]), "uploaded": False}
        )
    )


if __name__ == "__main__":
    main()
