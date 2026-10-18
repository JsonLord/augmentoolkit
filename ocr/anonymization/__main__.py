"""Trusted local workflow: OCRResult JSON -> candidate JSON. No release shortcut."""

import argparse
import os
from pathlib import Path

from ocr.handoff import anonymization_handoff
from ocr.schemas import OCRResult, SafeError

from .engine import AnonymizationEngine
from .recognizer import GLiNERRecognizer


def main():
    parser = argparse.ArgumentParser(
        description="Local OCR anonymization candidate (review required)"
    )
    parser.add_argument("--model-cache", required=True)
    parser.add_argument(
        "--input", required=True, help="Classified OCRResult JSON; authorized local filesystem only"
    )
    parser.add_argument(
        "--output", required=True, help="New candidate JSON file (still classified)"
    )
    args = parser.parse_args()
    try:
        path = Path(args.input)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise SafeError("anonymization_input_limit")
        evidence = OCRResult.model_validate_json(path.read_bytes())
        model = GLiNERRecognizer(args.model_cache)
        candidate = anonymization_handoff(evidence, AnonymizationEngine(model))
        # Create exclusively, owner read/write; never overwrite classified evidence.
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(candidate.model_dump_json(indent=2) + "\n")
    except Exception:
        raise SystemExit("anonymization_candidate_failed") from None
    print("candidate_created_review_required")


if __name__ == "__main__":
    main()
