"""Host security contract. No identity provider or request-identity fallback."""

import time
from typing import Protocol

from pydantic import Field

from ocr.schemas import SafeError

from .release_policy import ReleaseAuthority
from .schemas import Destination, StrictModel, VerifiedReview


class AuthenticatedIdentity(StrictModel):
    subject: str = Field(pattern=r"^[a-zA-Z0-9:_-]{1,128}$")
    session_id: str = Field(pattern=r"^[a-zA-Z0-9:_-]{1,128}$")
    expires_at: int


class CompletedReview(StrictModel):
    attestation: VerifiedReview
    completed: bool
    revoked: bool
    expires_at: int


class ExportAuthorizationRecord(StrictModel):
    artifact_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_id: str
    review_evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    sensitivity: str
    destination_id: str
    expires_at: int
    revoked: bool


class HostSecurityAdapter(Protocol):
    """Implement against authenticated server context and authoritative stores.

    credentials are opaque authentication evidence, never a requested subject.
    Review content/evidence must be immutable; revocation is separate mutable state.
    Host ACLs and destination policy are looked up on EVERY operation.
    """

    def authenticate(self, credential: object) -> AuthenticatedIdentity: ...
    def load_review(self, review_id: str) -> CompletedReview: ...
    def candidate_exists(self, candidate_digest: str) -> bool: ...
    def load_export(self, artifact_digest: str) -> ExportAuthorizationRecord: ...
    def current_acl(
        self, identity: AuthenticatedIdentity, scope: str, sensitivity: str, destination_id: str
    ) -> bool: ...
    def destination(self, destination_id: str) -> Destination: ...


class HostAuthorizationBridge:
    def __init__(self, host: HostSecurityAdapter):
        self.host = host

    def identity(self, credential):
        identity = AuthenticatedIdentity.model_validate(self.host.authenticate(credential))
        if identity.expires_at <= time.time():
            raise SafeError("host_authorization_denied")
        return identity

    def review(self, credential, review_id):
        try:
            identity = self.identity(credential)
            record = CompletedReview.model_validate(self.host.load_review(review_id))
            review = record.attestation
            if (
                not record.completed
                or record.revoked
                or record.expires_at <= time.time()
                or review.reviewer_subject != identity.subject
                or review.review_id != review_id
                or self.host.candidate_exists(review.candidate_digest) is not True
            ):
                raise ValueError()
            destination = Destination.model_validate(self.host.destination(review.destination_id))
            if (
                self.host.current_acl(
                    identity,
                    "anonymization:release",
                    review.sensitivity,
                    destination.destination_id,
                )
                is not True
            ):
                raise ValueError()
            return review
        except Exception:
            raise SafeError("host_authorization_denied") from None

    def acl(self, review, scope, destination):
        # Identity/ACL already checked with the current session by review(). This
        # extra check binds the live operator policy to the approval snapshot.
        return (
            Destination.model_validate(self.host.destination(destination.destination_id))
            == destination
        )

    def authority(self, key, ledger, destination_ids, **kwargs):
        return ReleaseAuthority(
            key,
            self.acl,
            verify_review=self.review,
            consume_approval=ledger.consume,
            destinations=tuple(self.host.destination(i) for i in destination_ids),
            **kwargs,
        )

    def transfer_authorized(self, credential, export_digest, sensitivity, destination_id):
        """Separate current permission; a release approval alone never calls this."""
        try:
            identity = self.identity(credential)
            destination = Destination.model_validate(self.host.destination(destination_id))
            if (
                destination.destination_class != "private"
                or sensitivity not in destination.allowed_sensitivities
            ):
                return False
            record = ExportAuthorizationRecord.model_validate(self.host.load_export(export_digest))
            review = CompletedReview.model_validate(self.host.load_review(record.review_id))
            if (
                record.artifact_digest != export_digest
                or record.sensitivity != sensitivity
                or record.destination_id != destination_id
                or record.revoked
                or record.expires_at <= time.time()
                or not review.completed
                or review.revoked
                or review.expires_at <= time.time()
                or review.attestation.candidate_digest != record.candidate_digest
                or review.attestation.evidence_digest != record.review_evidence_digest
                or review.attestation.sensitivity != sensitivity
                or review.attestation.destination_id != destination_id
                or self.host.candidate_exists(record.candidate_digest) is not True
            ):
                return False
            return (
                self.host.current_acl(
                    identity, "anonymization:private-transfer", sensitivity, destination_id
                )
                is True
            )
        except Exception:
            raise SafeError("host_authorization_denied") from None
