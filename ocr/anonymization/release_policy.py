"""Private, destination-bound, single-use reviewed export. No transmission API.

The host supplies authenticated review verification, ACL authorization, an
operator-controlled destination registry and atomic persistent replay storage.
There are no permissive defaults or built-in identity-provider claims.
"""

import hashlib
import hmac
import json
import time
import uuid

from ocr.schemas import SafeError

from .schemas import (
    Approval,
    CorrectedCandidate,
    Destination,
    LegacyReleaseCandidate,
    ReleaseCandidate,
    VerifiedReview,
)


def digest(candidate):
    try:
        if type(candidate) not in (LegacyReleaseCandidate, ReleaseCandidate, CorrectedCandidate):
            raise ValueError()
        validated = type(candidate).model_validate(candidate)
        if not validated.pages or any(
            p.original_sensitivity != validated.sensitivity for p in validated.pages
        ):
            raise ValueError()
        return hashlib.sha256(validated.model_dump_json().encode()).hexdigest()
    except Exception:
        raise SafeError("invalid_release_candidate") from None


class ReleaseAuthority:
    def __init__(
        self,
        key: bytes,
        authorize,
        *,
        verify_review,
        consume_approval,
        destinations,
        ttl=900,
        correction_policy=None,
        legacy_policy=None,
    ):
        if (
            not isinstance(key, bytes)
            or len(key) < 32
            or not all(callable(c) for c in (authorize, verify_review, consume_approval))
            or (correction_policy is not None and not callable(correction_policy))
            or (legacy_policy is not None and not callable(legacy_policy))
            or type(ttl) is not int
            or not 1 <= ttl <= 900
        ):
            raise SafeError("invalid_release_authority")
        try:
            entries = tuple(Destination.model_validate(d) for d in destinations)
            self.destinations = {d.destination_id: d for d in entries}
            if not entries or len(self.destinations) != len(entries):
                raise ValueError()
        except Exception:
            raise SafeError("invalid_release_destinations") from None
        self._key, self._authorize = key, authorize
        self._verify_review, self._consume = verify_review, consume_approval
        self.ttl = ttl
        self._correction_policy = correction_policy
        self._legacy_policy = legacy_policy

    def _destination(self, identifier, sensitivity, format):
        if format not in ("json", "text"):
            raise SafeError("unsupported_release_export")
        if not isinstance(identifier, str) or identifier not in self.destinations:
            raise SafeError("release_destination_not_approved")
        destination = self.destinations[identifier]
        if sensitivity not in destination.allowed_sensitivities or (
            sensitivity != "D0" and destination.destination_class != "private"
        ):
            raise SafeError("release_destination_not_approved")
        if sensitivity != "D0" and format != "json":
            raise SafeError("classified_export_requires_json")
        return destination

    def _review(self, actor, review_id, value, level, destination):
        try:
            # The callback must verify the session/actor and completed review record,
            # not simply echo values from the request or accept a completed=True flag.
            review = VerifiedReview.model_validate(self._verify_review(actor, review_id))
            now = int(time.time())
            if (
                review.review_id != review_id
                or review.candidate_digest != value
                or review.sensitivity != level
                or review.destination_id != destination.destination_id
                or not 0 <= now - review.completed_at < self.ttl
                or self._authorize(review, "anonymization:release", destination) is not True
            ):
                raise ValueError()
            return review
        except Exception:
            raise SafeError("release_not_approved") from None

    def _candidate_policy(self, candidate, review):
        try:
            if type(candidate) is CorrectedCandidate:
                if review.evidence_digest != candidate.correction.original_evidence_digest:
                    raise ValueError()
                policy = self._correction_policy
            else:
                # v1/v2 do not carry correction history. The operator must
                # explicitly attest authoritative schema/source eligibility;
                # removing v3 metadata cannot select an implicit allow branch.
                policy = self._legacy_policy
            if policy is None or policy(candidate, review) is not True:
                raise ValueError()
        except Exception:
            raise SafeError("release_not_approved") from None

    def _signature(self, fields):
        payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def approve(self, candidate, *, actor, review_id, destination, format="json"):
        value = digest(candidate)
        target = self._destination(destination, candidate.sensitivity, format)
        review = self._review(actor, review_id, value, candidate.sensitivity, target)
        self._candidate_policy(candidate, review)
        now = int(time.time())
        fields = dict(
            schema_version="ocr-review-approval/v2",
            approval_id=uuid.uuid4().hex,
            candidate_digest=value,
            review=review.model_dump(mode="json"),
            destination=target.model_dump(mode="json"),
            format=format,
            issued_at=now,
            expires_at=min(now + self.ttl, review.completed_at + self.ttl),
        )
        return Approval.model_validate_json(
            json.dumps(fields | {"signature": self._signature(fields)})
        )

    def export(self, candidate, approval=None, *, actor, destination, format="json"):
        value = digest(candidate)
        target = self._destination(destination, candidate.sensitivity, format)
        try:
            approval = Approval.model_validate(approval)
            now = int(time.time())
            if (
                approval.candidate_digest != value
                or approval.destination != target
                or approval.format != format
                or not approval.issued_at <= now < approval.expires_at
                or approval.expires_at > approval.issued_at + self.ttl
                or not hmac.compare_digest(
                    approval.signature,
                    self._signature(approval.model_dump(mode="json", exclude={"signature"})),
                )
            ):
                raise ValueError()
        except Exception:
            raise SafeError("release_not_approved") from None
        review = self._review(
            actor, approval.review.review_id, value, candidate.sensitivity, target
        )
        self._candidate_policy(candidate, review)
        if review != approval.review:
            raise SafeError("release_not_approved")
        text = "\n\n".join(p.anonymized_text for p in candidate.pages)
        output = (
            text
            if format == "text"
            else json.dumps(
                dict(
                    schema_version="ocr-reviewed-export/v2",
                    text=text,
                    sensitivity=candidate.sensitivity,
                    anonymous=False,
                    release_review_required=False,
                    release_approved=True,
                    candidate_digest=value,
                    destination_id=target.destination_id,
                    destination_class=target.destination_class,
                    transmission_authorized=False,
                ),
                ensure_ascii=False,
            )
        )
        try:
            # Consume AFTER serialization, BEFORE returning bytes. A crash after
            # consumption burns the approval. Reissue requires authorization again.
            if self._consume(approval.approval_id, approval.expires_at) is not True:
                raise ValueError()
        except Exception:
            raise SafeError("release_approval_consumed_or_unavailable") from None
        # Persistent consumption can wait for a database lock. Reauthorize
        # after it completes, before classified bytes leave this boundary. A
        # failed final check burns the nonce; it never rolls the ledger back.
        final_review = self._review(
            actor, approval.review.review_id, value, candidate.sensitivity, target
        )
        self._candidate_policy(candidate, final_review)
        if final_review != approval.review or not (
            approval.issued_at <= int(time.time()) < approval.expires_at
        ):
            raise SafeError("release_not_approved")
        return output
