"""Disposable parser process: bounded CPU/address space, no credential environment."""

import base64
import json
import resource
import sys

# Limits are set before importing native document/image libraries.
resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))

from .config import Settings  # noqa: E402
from .ingest import ingest  # noqa: E402
from .schemas import OCROptions, SafeError  # noqa: E402


def main():
    try:
        raw = sys.stdin.buffer.read(36 * 1024 * 1024 + 1)
        if len(raw) > 36 * 1024 * 1024:
            raise SafeError("upload_limit")
        payload = json.loads(raw)
        pages = ingest(
            base64.b64decode(payload["data"], validate=True),
            payload["filename"],
            OCROptions.model_validate(payload["options"]),
            Settings(**payload["limits"]),
        )
        out = [
            dict(
                index=p.index,
                image=base64.b64encode(p.image).decode(),
                width=p.width,
                height=p.height,
                page_id=p.page_id,
                rotation=p.rotation,
                embedded=p.embedded,
                features=p.features,
            )
            for p in pages
        ]
        sys.stdout.write(json.dumps({"pages": out}))
    except Exception as e:
        sys.stdout.write(
            json.dumps({"error": e.code if isinstance(e, SafeError) else "invalid_document"})
        )


if __name__ == "__main__":
    main()
