"""Verify immutable historical artifacts; never evaluate updated code as an old holdout."""

import hashlib
import json
from pathlib import Path

FROZEN_ARTIFACT_SHA256 = {
    "docs/anonymization-evaluation/gate03c-holdout-first.json": "257daa6d43285925c4899471c3f615293293fa0fa8ed17545ebb9d8e99802e83",
    "docs/anonymization-evaluation/gate03c-holdout-first.provenance.json": "bf19f6ae26e146aa6e04aaed9a54705c0f910af308ea33a8c5eacfe4d1724fbb",
    "docs/anonymization-evaluation/gate03d-holdout-first.json": "08f86d2b78871683835c51b627e251c66b83fc33f828d8a6c8e9742c8bd47d9f",
    "docs/anonymization-evaluation/gate03d-holdout-first.provenance.json": "9d5fa15c5427fcfa499262b20453ed4adfe59da86709dffc745ec205e0661bca",
    "ocr/anonymization/fixtures/holdout03c/cases.json": "0174fb41d34d0a4db18877c68fb70149eaa67c05da86608d5da4ff8765a52c15",
    "ocr/anonymization/fixtures/holdout03c/freeze.json": "c707239993099dab4a48b83f6c2aac58674de5423ede75cd1aced17d290708a6",
    "ocr/anonymization/fixtures/holdout03d/cases.json": "7180e176f6d8bd259da56ad729ede6fd2c798aca15a786ba0ff0adcd862f21a1",
    "ocr/anonymization/fixtures/holdout03d/freeze.json": "448dc5d7b0f3ef00f7d0f90084866f7f2e85311aac6af32ed0ad3d2658f8b1f5",
}


def verify_historical():
    repo = Path(__file__).parents[2]
    for name, value in FROZEN_ARTIFACT_SHA256.items():
        if hashlib.sha256((repo / name).read_bytes()).hexdigest() != value:
            raise ValueError("historical_evidence_changed")
    root = Path(__file__).parent / "fixtures/holdout03d"
    freeze = json.loads((root / "freeze.json").read_bytes())
    report = json.loads(
        (repo / "docs/anonymization-evaluation/gate03d-holdout-first.json").read_bytes()
    )
    cases = json.loads((root / "cases.json").read_bytes())
    if (
        report["implementation_sha256"] != freeze["implementation_sha256"]
        or report["corpus_sha256"] != freeze["sha256"]
        or {p["case_id"] for p in report["per_case"]} != {c["id"] for c in cases}
    ):
        raise ValueError("historical_evidence_changed")
    return root, freeze, cases, report
