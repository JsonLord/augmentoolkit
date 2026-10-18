"""Explicit review authorization, bound to immutable candidate bytes.

The embedding private application supplies identity/authorization. There is no
HTTP release endpoint and no default permissive authorizer.
"""

import hashlib
import hmac
import json
import time

from ocr.schemas import SafeError

from .schemas import Approval, ReleaseCandidate


def digest(candidate):
    if not isinstance(candidate, ReleaseCandidate):
        raise SafeError("invalid_release_candidate")
    return hashlib.sha256(candidate.model_dump_json().encode()).hexdigest()


class ReleaseAuthority:
    def __init__(self, key: bytes, authorize):
        if not isinstance(key, bytes) or len(key) < 32 or not callable(authorize):
            raise SafeError("invalid_release_authority")
        self._key, self._authorize = key, authorize

    def _signature(self, value, expiry):
        return hmac.new(self._key, f"{value}:{expiry}".encode(), hashlib.sha256).hexdigest()

    def approve(self, candidate, *, actor, review_completed: bool):
        """Caller must have reviewed this exact candidate, including remaining identifiers."""
        value = digest(candidate)
        try:
            permitted = self._authorize(
                actor, "anonymization:release", value, candidate.sensitivity
            )
        except Exception:
            raise SafeError("release_authorization_failed") from None
        if permitted is not True or review_completed is not True:
            raise SafeError("release_not_approved")
        expiry = int(time.time()) + 900
        return Approval(
            candidate_digest=value,
            expires_at=expiry,
            signature=self._signature(value, expiry),
        )

    def export(self, candidate, approval=None, *, format="json"):
        if format not in ("json", "text"):
            raise SafeError("unsupported_release_export")
        value = digest(candidate)
        if (
            not isinstance(approval, Approval)
            or approval.candidate_digest != value
            or approval.expires_at <= time.time()
            or not hmac.compare_digest(
                approval.signature, self._signature(value, approval.expires_at)
            )
        ):
            raise SafeError("release_not_approved")
        text = "\n\n".join(p.anonymized_text for p in candidate.pages)
        # Preserve source classification. Approval does not assert complete anonymity.
        if format == "text":
            # Bare text has no classification container; intentionally unsupported for D1/D2.
            if candidate.sensitivity != "D0":
                raise SafeError("classified_export_requires_json")
            return text
        return json.dumps(
            {
                "schema_version": "ocr-reviewed-export/v1",
                "text": text,
                "sensitivity": candidate.sensitivity,
                "anonymous": False,
                "release_review_required": False,
                "release_approved": True,
                "candidate_digest": value,
            },
            ensure_ascii=False,
        )
