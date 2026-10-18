"""ISOLATED synthetic correction store/identity fixture. NOT durable production storage.

Transactions use an RLock/CAS only. Production must independently qualify durable
atomic source/candidate/proposal retention and current authorization inside commits.
"""

import threading
import time

from ocr.anonymization.corrections import (
    CorrectionContext,
    CorrectionProposal,
    PublicationReceipt,
    StoredProposal,
    author_digest,
    commitment,
)
from ocr.anonymization.host_adapter import AuthenticatedIdentity, CompletedReview
from ocr.anonymization.release_policy import digest
from ocr.anonymization.schemas import CorrectedCandidate, Destination, VerifiedReview


class CorrectionHostFixture:
    production_ready = False
    durable_corrections = False

    def __init__(self, context, texts, *, separate_approver):
        if type(separate_approver) is not bool:
            raise ValueError()
        self.separate_approver = separate_approver
        self.lock = threading.RLock()
        self.author_credential, self.approver_credential = object(), object()
        self.identities = {
            self.author_credential: AuthenticatedIdentity(
                subject="synthetic-author",
                session_id="author-session",
                expires_at=int(time.time()) + 600,
            ),
            self.approver_credential: AuthenticatedIdentity(
                subject="synthetic-approver",
                session_id="approver-session",
                expires_at=int(time.time()) + 600,
            ),
        }
        self.contexts = {context.material.binding.review_ref: context}
        self.bindings = {context.material.binding.review_ref: context.material.binding}
        self.heads = {commitment(context.machine): commitment(context)}
        self.source = {
            (s.evidence_ref, s.page_index): texts[s.page_index].encode()
            for s in context.machine.sources
        }
        self.candidates = {}
        if context.material.candidate is not None:
            self.candidates[digest(context.material.candidate)] = context.material.candidate
        self.proposals, self.completed, self.exports = {}, {}, {}
        self.allowed = set()
        binding = context.material.binding
        for identity in self.identities.values():
            self.allowed.add(
                (
                    identity.subject,
                    "anonymization:review",
                    binding.sensitivity,
                    binding.environment_id,
                )
            )
            self.allowed.add(
                (
                    identity.subject,
                    "anonymization:release",
                    binding.sensitivity,
                    "synthetic-private",
                )
            )
        for action in ("inspect", "submit", "apply"):
            self.allowed.add(
                (
                    "synthetic-author",
                    "anonymization:correction:" + action,
                    binding.sensitivity,
                    binding.environment_id,
                )
            )
        self.policy = Destination(
            destination_id="synthetic-private",
            destination_class="private",
            allowed_sensitivities=("D0", "D1", "D2"),
        )
        self.context_reads, self.proposal_reads = 0, 0
        self.before_publish = None

    def authenticate(self, credential):
        return self.identities[credential]

    def current_acl(self, identity, scope, sensitivity, destination):
        return (identity.subject, scope, sensitivity, destination) in self.allowed

    def private_environment(self, environment):
        return environment == "synthetic-room"

    def review_binding(self, reference):
        return self.bindings[reference]

    def load_review_material(self, reference):
        return self.contexts[reference].material

    def read_classified_page(self, reference, index):
        return self.source[(reference, index)]

    def candidate_exists(self, value):
        return value in self.candidates

    def load_correction_context(self, reference):
        self.context_reads += 1
        return self.contexts[reference]

    def correction_binding(self, identifier):
        return self.proposals[identifier].proposal.review_ref

    def load_correction(self, identifier):
        self.proposal_reads += 1
        return self.proposals[identifier]

    def destination(self, identifier):
        if identifier != self.policy.destination_id:
            raise KeyError()
        return self.policy

    def load_review(self, identifier):
        return self.completed[identifier]

    def load_export(self, value):
        return self.exports[value]

    def _check(self, credential, proposal, expected, action):
        identity = self.authenticate(credential)
        context = self.contexts[proposal.review_ref]
        binding = self.review_binding(proposal.review_ref)
        if (
            identity.subject != proposal.author_subject
            or identity.expires_at <= time.time()
            or binding != context.material.binding
            or binding.revoked
            or binding.expires_at <= time.time()
            or not self.private_environment(binding.environment_id)
            or not self.current_acl(
                identity, "anonymization:review", binding.sensitivity, binding.environment_id
            )
            or not self.current_acl(
                identity,
                "anonymization:correction:" + action,
                binding.sensitivity,
                binding.environment_id,
            )
            or commitment(context) != expected
            or proposal.context_digest != expected
            or self.heads[commitment(context.machine)] != expected
            or not proposal.created_at <= time.time() < proposal.expires_at
        ):
            raise PermissionError("synthetic commit denied")
        return context

    def store_correction(self, credential, proposal, expected_context):
        with self.lock:
            proposal = CorrectionProposal.model_validate(proposal)
            self._check(credential, proposal, expected_context, "submit")
            if proposal.correction_id in self.proposals:
                raise ValueError()
            self.proposals[proposal.correction_id] = StoredProposal(
                proposal=proposal, revoked=False, published_candidate_digest=None
            )
            return True

    def publish_correction(self, credential, proposal, context, expected_context):
        with self.lock:
            if self.before_publish is not None:
                self.before_publish()
            old = self._check(credential, proposal, expected_context, "apply")
            context = CorrectionContext.model_validate(context)
            record = self.proposals[proposal.correction_id]
            candidate = context.material.candidate
            if (
                record.proposal != proposal
                or record.revoked
                or record.published_candidate_digest is not None
                or context.machine != old.machine
                or type(candidate) is not CorrectedCandidate
                or candidate.correction.proposal_digest != commitment(proposal)
                or candidate.correction.prior_candidate_digest != old.material.candidate_digest
                or context.material.binding.sensitivity != old.material.binding.sensitivity
                or context.material.binding.environment_id != old.material.binding.environment_id
                or context.material.binding.expires_at != old.material.binding.expires_at
                or context.material.binding.review_ref in self.contexts
            ):
                raise ValueError()
            value = digest(candidate)
            reference = context.material.binding.review_ref
            self.contexts[reference] = context
            self.bindings[reference] = context.material.binding
            self.candidates[value] = candidate
            self.proposals[proposal.correction_id] = record.model_copy(
                update={"published_candidate_digest": value, "published_review_ref": reference}
            )
            self.heads[commitment(context.machine)] = commitment(context)
            return PublicationReceipt(
                correction_id=proposal.correction_id,
                proposal_digest=commitment(proposal),
                candidate_digest=value,
                context_digest=commitment(context),
                review_ref=reference,
            )

    def complete_review(self, credential, value):
        """Privileged synthetic test setup ONLY, not a completion API/default."""
        identity = self.authenticate(credential)
        candidate = self.candidates[value]
        context = next(c for c in self.contexts.values() if c.material.candidate_digest == value)
        reference = "completed-" + str(len(self.completed))
        self.completed[reference] = CompletedReview(
            attestation=VerifiedReview(
                reviewer_subject=identity.subject,
                review_id=reference,
                evidence_digest=context.machine.evidence_digest,
                candidate_digest=value,
                sensitivity=candidate.sensitivity,
                destination_id=self.policy.destination_id,
                completed_at=int(time.time()),
            ),
            completed=True,
            revoked=False,
            expires_at=int(time.time()) + 600,
        )
        return reference

    def corrected_release_allowed(self, candidate, review):
        if (
            self.candidates.get(digest(candidate)) != candidate
            or type(candidate) is not CorrectedCandidate
        ):
            return False
        return (
            not self.separate_approver
            or author_digest(review.reviewer_subject)
            not in candidate.correction.author_subject_digests
        )
