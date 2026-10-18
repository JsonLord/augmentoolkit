"""Explicit candidate version dispatch. No rewriting, migration or approval copying."""

import json

from ocr.schemas import SafeError

from .schemas import CorrectedCandidate, LegacyReleaseCandidate, ReleaseCandidate


def parse_candidate(data):
    try:
        if not isinstance(data, (str, bytes)) or len(data) > 16 * 1024 * 1024:
            raise ValueError()
        version = json.loads(data)["schema_version"]
        cls = {
            "ocr-anonymization-candidate/v1": LegacyReleaseCandidate,
            "ocr-anonymization-candidate/v2": ReleaseCandidate,
            "ocr-anonymization-candidate/v3": CorrectedCandidate,
        }[version]
        return cls.model_validate_json(data)
    except Exception:
        raise SafeError("unsupported_or_invalid_candidate") from None
