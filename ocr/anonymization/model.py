"""Pinned artifacts for native GLiNER; setup is the only network-enabled operation."""

import hashlib
import json
from pathlib import Path

from ocr.schemas import SafeError

MODEL_ID = "VAGOsolutions/SauerkrautLM-GLiNER"
MODEL_REVISION = "ba967ee030b7e679dd5422cb16fcd1b379da38e1"
FILES = (
    "gliner_config.json",
    "pytorch_model.bin",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
)


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_cache(path):
    try:
        path = Path(path)
        manifest = json.loads((path / "manifest.json").read_text())
        expected = json.loads(Path(__file__).with_name("artifacts.json").read_text())
        if manifest != expected:
            raise ValueError()
        if manifest["model"] != MODEL_ID or manifest["revision"] != MODEL_REVISION:
            raise ValueError()
        if set(manifest["files"]) != set(FILES):
            raise ValueError()
        for name in FILES:
            if file_hash(path / name) != manifest["files"][name]:
                raise ValueError()
        return path
    except Exception:
        raise SafeError("anonymization_model_unavailable") from None


def install(path):
    """Explicit operator command. Download the fixed snapshot, record file integrity."""
    from huggingface_hub import snapshot_download

    path = Path(path)
    if (path / "manifest.json").exists():
        return verify_cache(path)
    if path.exists() and any(p.name not in {*FILES, ".cache"} for p in path.iterdir()):
        raise SafeError("anonymization_model_setup_directory_not_empty")
    snapshot_download(
        MODEL_ID,
        revision=MODEL_REVISION,
        local_dir=path,
        allow_patterns=list(FILES),
    )
    manifest = {
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "files": {name: file_hash(path / name) for name in FILES},
    }
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return verify_cache(path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Explicit pinned local anonymization model setup")
    parser.add_argument("--cache", required=True)
    args = parser.parse_args()
    try:
        install(args.cache)
    except Exception:
        raise SystemExit("anonymization_model_setup_failed") from None
    print(f"Cached {MODEL_ID}@{MODEL_REVISION}")
