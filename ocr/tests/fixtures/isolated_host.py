"""ISOLATED TEST ADAPTER ONLY. Not a production identity provider or startup default."""

import hashlib
import time
from types import MappingProxyType

from ocr.anonymization.host_adapter import AuthenticatedIdentity, CompletedReview
from ocr.anonymization.release_policy import digest
from ocr.anonymization.schemas import Destination, VerifiedReview


class IsolatedHostAdapter:
    production_ready = False

    def __init__(self, candidate):
        self.credential = object()  # Never accepted from a requested subject string.
        self.identity = AuthenticatedIdentity(
            subject="fixture-subject",
            session_id="fixture-session",
            expires_at=int(time.time()) + 600,
        )
        self.policy = Destination(
            destination_id="private-fixture",
            destination_class="private",
            allowed_sensitivities=("D0", "D1", "D2"),
        )
        self.destinations = {self.policy.destination_id: self.policy}
        value = digest(candidate)
        attestation = VerifiedReview(
            reviewer_subject=self.identity.subject,
            review_id="fixture-review",
            evidence_digest=hashlib.sha256(b"immutable synthetic completed review").hexdigest(),
            candidate_digest=value,
            sensitivity=candidate.sensitivity,
            destination_id=self.policy.destination_id,
            completed_at=int(time.time()),
        )
        self.records = MappingProxyType(
            {
                attestation.review_id: CompletedReview(
                    attestation=attestation,
                    completed=True,
                    revoked=False,
                    expires_at=int(time.time()) + 600,
                )
            }
        )
        self.candidates = frozenset((value,))
        self.exports = {}
        self.revoked = set()
        self.allowed = {
            (
                self.identity.subject,
                "anonymization:release",
                candidate.sensitivity,
                self.policy.destination_id,
            )
        }

    def authenticate(self, credential):
        if credential is not self.credential:
            raise PermissionError("fixture_credentials_rejected")
        return self.identity

    def load_review(self, review_id):
        return self.records[review_id].model_copy(update={"revoked": review_id in self.revoked})

    def candidate_exists(self, candidate_digest):
        return candidate_digest in self.candidates

    def export_exists(self, artifact_digest, sensitivity, destination_id):
        return self.exports.get(artifact_digest) == (sensitivity, destination_id)

    def current_acl(self, identity, scope, sensitivity, destination_id):
        return (identity.subject, scope, sensitivity, destination_id) in self.allowed

    def destination(self, destination_id):
        return self.destinations[destination_id]
