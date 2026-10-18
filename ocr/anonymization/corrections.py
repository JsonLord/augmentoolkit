"""Source-bound, authorized human corrections. No recognition optimization/export.

Machine snapshots, proposals and published effective states are immutable private
records. The host must implement durable atomic CAS publication and current ACLs;
no production identity/store is supplied. Candidate metadata contains hashes only.
"""

import hashlib
import json
import re
import time
import uuid
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ocr.schemas import OCRResult, SafeError

from .private_review import (
    PrivateReviewBridge,
    PrivateReviewHost,
    PrivateReviewMaterial,
    ReviewBinding,
    SourcePageReference,
)
from .provenance import provenance
from .release_policy import digest
from .replacement import replace, validate_entities
from .schemas import (
    LABELS,
    CandidatePage,
    CorrectedCandidate,
    CorrectionProvenance,
    Entity,
    Label,
    RecognitionProvenance,
    ReleaseCandidate,
    StrictModel,
)

POLICY = "reviewed-corrections-v1"


def commitment(value):
    if isinstance(value, StrictModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode()
    ).hexdigest()


def author_digest(subject):
    return commitment(dict(domain="private-correction-author/v1", subject=subject))


class Observation(StrictModel):
    observation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_index: int = Field(ge=0)
    entity: Entity = Field(repr=False)
    human_correction_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def explicit_human_provenance(self):
        if self.human_correction_id is not None and (
            self.entity.source != "deterministic"
            or self.entity.model_revision != POLICY
            or self.entity.score is not None
            or self.entity.normalization != "identity"
        ):
            raise ValueError("invalid_human_observation")
        return self


class Placeholder(StrictModel):
    category: Label
    token: str = Field(pattern=r"^\[[A-Z_]{1,40}\]$")


class MachineSnapshot(StrictModel):
    schema_version: Literal["private-machine-snapshot/v1"] = "private-machine-snapshot/v1"
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    sources: tuple[SourcePageReference, ...] = Field(min_length=1, max_length=100, repr=False)
    observations: tuple[Observation, ...] = Field(max_length=100_000, repr=False)
    provenance: RecognitionProvenance
    placeholders: tuple[Placeholder, ...]
    page_sources: tuple[Literal["embedded_text", "cpu_ocr", "alias_ocr"], ...]
    machine_candidate: ReleaseCandidate | None = Field(repr=False)

    @model_validator(mode="after")
    def machine_only(self):
        if (
            any(o.human_correction_id is not None for o in self.observations)
            or {p.category for p in self.placeholders} != set(LABELS)
            or len(self.placeholders) != len(LABELS)
            or len(self.sources) != len(self.page_sources)
            or len({s.page_index for s in self.sources}) != len(self.sources)
            or sum(s.characters for s in self.sources) > 2_000_000
            or len({s.sensitivity for s in self.sources}) != 1
            or (
                self.machine_candidate is not None
                and type(self.machine_candidate) is not ReleaseCandidate
            )
        ):
            raise ValueError("invalid_machine_snapshot")
        return self


class CorrectionContext(StrictModel):
    schema_version: Literal["private-correction-context/v1"] = "private-correction-context/v1"
    material: PrivateReviewMaterial = Field(repr=False)
    machine: MachineSnapshot = Field(repr=False)
    effective: tuple[Observation, ...] = Field(max_length=100_000, repr=False)

    @model_validator(mode="after")
    def root_binding(self):
        if (
            self.material.evidence_digest != self.machine.evidence_digest
            or self.material.sources != self.machine.sources
            or self.material.provenance != self.machine.provenance
        ):
            raise ValueError("correction_context_mismatch")
        candidate = self.material.candidate
        if type(candidate) is CorrectedCandidate:
            c = candidate.correction
            if (
                c.machine_snapshot_digest != commitment(self.machine)
                or c.original_evidence_digest != self.machine.evidence_digest
                or c.source_references_digest
                != commitment([s.model_dump(mode="json") for s in self.machine.sources])
                or c.effective_state_digest != state_digest(self.machine, self.effective)
            ):
                raise ValueError("correction_context_mismatch")
        elif (
            self.effective != self.machine.observations
            or candidate != self.machine.machine_candidate
        ):
            raise ValueError("correction_context_mismatch")
        if candidate is None and self.material.blocking_reason != "conflicting_spans":
            raise ValueError("unsupported_failed_correction_context")
        return self


def state_digest(machine, effective):
    return commitment(
        dict(
            machine=commitment(machine), observations=[o.model_dump(mode="json") for o in effective]
        )
    )


class SpanBinding(StrictModel):
    page_index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    source_span_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class CorrectionEdit(StrictModel):
    span: SpanBinding
    category: Label
    action: Literal["mask", "suppress"]
    rationale: Literal[
        "missing_entity", "incomplete_boundary", "wrong_category", "span_conflict", "false_positive"
    ]
    target_observation_ids: tuple[str, ...] = Field(max_length=100_000)

    @model_validator(mode="after")
    def valid_action(self):
        if (
            any(not re.fullmatch(r"[a-f0-9]{64}", s) for s in self.target_observation_ids)
            or len(set(self.target_observation_ids)) != len(self.target_observation_ids)
            or (self.action == "suppress") != (self.rationale == "false_positive")
            or (not self.target_observation_ids) != (self.rationale == "missing_entity")
        ):
            raise ValueError("invalid_correction_action")
        return self


class CorrectionProposal(StrictModel):
    schema_version: Literal["private-ocr-correction/v1"] = "private-ocr-correction/v1"
    policy_version: Literal["reviewed-corrections-v1"] = "reviewed-corrections-v1"
    correction_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    review_ref: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    author_subject: str = Field(pattern=r"^[a-zA-Z0-9:_-]{1,128}$", repr=False)
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    machine_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    prior_candidate_digest: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    provenance: RecognitionProvenance
    edits: tuple[CorrectionEdit, ...] = Field(min_length=1, max_length=1000)
    created_at: int
    expires_at: int

    @model_validator(mode="after")
    def short_lived(self):
        if not 0 <= self.created_at < self.expires_at <= self.created_at + 900:
            raise ValueError("invalid_correction_expiration")
        return self


class StoredProposal(StrictModel):
    proposal: CorrectionProposal = Field(repr=False)
    revoked: bool
    published_candidate_digest: str | None = Field(pattern=r"^[a-f0-9]{64}$")

    published_review_ref: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,128}$")

    @model_validator(mode="after")
    def complete_publication_pointer(self):
        if (self.published_review_ref is None) != (self.published_candidate_digest is None):
            raise ValueError("invalid_publication_pointer")
        return self


class PublicationReceipt(StrictModel):
    correction_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_ref: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    automated_release_eligible: Literal[False] = False
    release_approved: Literal[False] = False


class CorrectionReview(StrictModel):
    context: CorrectionContext = Field(repr=False)
    record: StoredProposal = Field(repr=False)
    release_approved: Literal[False] = False


class CorrectionHost(PrivateReviewHost, Protocol):
    """Required host contract, NOT a durable storage implementation.

    store/publish must durably commit reauthorization, compare-and-swap of the
    active head, immutable records and single-use publication in one transaction.
    An RLock fixture demonstrates only in-process semantics. Crash/restart and
    multi-process durability remain unqualified until a real adapter is supplied.
    """

    def load_correction_context(self, review_ref: str) -> CorrectionContext: ...
    def correction_binding(self, correction_id: str) -> str: ...
    def load_correction(self, correction_id: str) -> StoredProposal: ...
    def store_correction(
        self, credential: object, proposal: CorrectionProposal, expected_context: str
    ) -> bool: ...
    def publish_correction(
        self,
        credential: object,
        proposal: CorrectionProposal,
        context: CorrectionContext,
        expected_context: str,
    ) -> PublicationReceipt: ...

    # Atomic operations must reauthorize and CAS the active context, retain all
    # old snapshots/proposals and consume this correction once in DURABLE storage.


def span_binding(context, page_index, start, end, text):
    source = next(s for s in context.machine.sources if s.page_index == page_index)
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start < end <= len(text)
        or len(text) != source.characters
        or hashlib.sha256(text.encode()).hexdigest() != source.sha256
    ):
        raise ValueError()
    value = commitment(
        dict(
            domain="classified-original-span/v1",
            evidence=context.machine.evidence_digest,
            page=source.model_dump(mode="json"),
            start=start,
            end=end,
            text_sha256=hashlib.sha256(text[start:end].encode()).hexdigest(),
        )
    )
    return SpanBinding(page_index=page_index, start=start, end=end, source_span_digest=value)


def render(machine, effective, texts):
    pages = []
    allowed_pages = {s.page_index for s in machine.sources}
    if any(o.page_index not in allowed_pages for o in effective):
        raise ValueError()
    for source, page_source in zip(machine.sources, machine.page_sources, strict=True):
        text = texts[source.page_index]
        if (
            len(text) != source.characters
            or hashlib.sha256(text.encode()).hexdigest() != source.sha256
        ):
            raise ValueError()
        observations = [o for o in effective if o.page_index == source.page_index]
        entities = validate_entities(text, [o.entity for o in observations])
        output, tags = replace(text, entities, {p.category: p.token for p in machine.placeholders})
        human = {
            (o.entity.start, o.entity.end, o.entity.label)
            for o in observations
            if o.human_correction_id is not None
        }
        tags = tuple(
            t.model_copy(update={"source": "human_correction"})
            if (t.start, t.end, t.label) in human and t.model_revision == POLICY
            else t
            for t in tags
        )
        pages.append(
            CandidatePage(
                page_index=source.page_index,
                anonymized_text=output,
                tags=tags,
                source=page_source,
                original_sensitivity=source.sensitivity,
            )
        )
    return tuple(pages)


def prepare_review(engine, result, *, review_ref, environment_id, expires_at):
    """Trusted capture. Runtime failure aborts; recoverable span conflicts are retained.

    The supplied review/env references are server-operator registration inputs, not
    public request fields. Return objects must be stored in an immutable private host.
    """
    try:
        snapshot = OCRResult.model_validate_json(result.model_dump_json())
        engine.validate_document(snapshot)
        original_digest = hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()
        config = provenance(
            engine.recognizer, engine.terminology, engine.status_policy, engine.placeholders
        )
        sources, observations, texts = [], [], {}
        for page in snapshot.pages:
            text = page.text
            texts[page.page_index] = text
            sources.append(
                SourcePageReference(
                    evidence_ref=commitment(dict(evidence=original_digest, page=page.page_index)),
                    page_index=page.page_index,
                    sensitivity=snapshot.sensitivity,
                    sha256=hashlib.sha256(text.encode()).hexdigest(),
                    characters=len(text),
                )
            )
            for entity in engine.observe(text):
                payload = dict(
                    evidence=original_digest,
                    config=config.configuration_digest,
                    page=page.page_index,
                    entity=entity.model_dump(mode="json"),
                )
                observations.append(
                    Observation(
                        observation_id=commitment(payload),
                        page_index=page.page_index,
                        entity=entity,
                    )
                )
        if config != provenance(
            engine.recognizer, engine.terminology, engine.status_policy, engine.placeholders
        ):
            raise ValueError()
        placeholders = (
            engine.placeholders
            if engine.placeholders is not None
            else {k: f"[{k}]" for k in LABELS}
        )
        machine = MachineSnapshot(
            evidence_digest=original_digest,
            sources=tuple(sources),
            observations=tuple(observations),
            provenance=config,
            placeholders=tuple(
                Placeholder(category=k, token=v) for k, v in sorted(placeholders.items())
            ),
            page_sources=tuple(p.source for p in snapshot.pages),
            machine_candidate=None,
        )
        blocked = None
        try:
            pages = render(machine, machine.observations, texts)
            reasons = ("recognition_incomplete_possible",) + (
                ("empty_prediction",)
                if any(
                    not any(
                        o.page_index == source.page_index and o.entity.source == "multilingual_ner"
                        for o in observations
                    )
                    for source in sources
                )
                else ()
            )
            candidate = ReleaseCandidate(
                candidate_id=uuid.uuid4().hex,
                sensitivity=snapshot.sensitivity,
                pages=pages,
                model_revision=config.model_revision,
                provenance=config,
                review_reasons=reasons,
            )
            machine = machine.model_copy(update={"machine_candidate": candidate})
        except SafeError as exc:
            if exc.code != "ambiguous_entity_overlap":
                raise
            candidate, blocked = None, "conflicting_spans"
        material = PrivateReviewMaterial(
            binding=ReviewBinding(
                review_ref=review_ref,
                environment_id=environment_id,
                sensitivity=snapshot.sensitivity,
                expires_at=expires_at,
                revoked=False,
            ),
            evidence_digest=original_digest,
            sources=tuple(sources),
            candidate=candidate,
            candidate_digest=digest(candidate) if candidate else None,
            provenance=config,
            blocking_reason=blocked,
        )
        return CorrectionContext(material=material, machine=machine, effective=machine.observations)
    except Exception:
        raise SafeError("correction_capture_failed") from None


def apply_edits(context, proposal, texts):
    if (
        proposal.context_digest != commitment(context)
        or proposal.evidence_digest != context.machine.evidence_digest
        or proposal.provenance != context.machine.provenance
        or proposal.machine_snapshot_digest != commitment(context.machine)
        or proposal.prior_candidate_digest != context.material.candidate_digest
        or proposal.review_ref != context.material.binding.review_ref
    ):
        raise ValueError()
    effective = {o.observation_id: o for o in context.effective}
    if any(effective[o.observation_id] != o for o in context.effective):
        raise ValueError()
    ordered = sorted(proposal.edits, key=lambda e: (e.span.page_index, e.span.start, e.span.end))
    used = set()
    previous = None
    for index, edit in enumerate(ordered):
        s = edit.span
        if previous is not None and previous.page_index == s.page_index and s.start < previous.end:
            raise SafeError("correction_conflict")
        previous = s
        if span_binding(context, s.page_index, s.start, s.end, texts[s.page_index]) != s:
            raise ValueError()
        targets = set(edit.target_observation_ids)
        if used & targets or not targets <= set(effective):
            raise SafeError("correction_conflict")
        used |= targets
        target_obs = [effective[k] for k in targets]
        overlap = {
            k
            for k, o in effective.items()
            if o.page_index == s.page_index and o.entity.start < s.end and s.start < o.entity.end
        }
        if edit.action == "mask":
            if targets != overlap:
                raise SafeError("correction_conflict")
            if edit.rationale == "wrong_category" and any(
                (o.entity.start, o.entity.end) != (s.start, s.end) for o in target_obs
            ):
                raise ValueError()
        elif any(
            o.page_index != s.page_index
            or o.entity.start != s.start
            or o.entity.end != s.end
            or o.entity.label != edit.category
            for o in target_obs
        ):
            raise ValueError()
        for key in targets:
            del effective[key]
        if edit.action == "mask":
            entity = Entity(
                start=s.start,
                end=s.end,
                label=edit.category,
                text=texts[s.page_index][s.start : s.end],
                source="deterministic",
                model_revision=POLICY,
            )
            key = commitment(
                dict(
                    correction=proposal.correction_id, ordinal=index, span=s.model_dump(mode="json")
                )
            )
            effective[key] = Observation(
                observation_id=key,
                page_index=s.page_index,
                entity=entity,
                human_correction_id=proposal.correction_id,
            )
    result = tuple(
        sorted(
            effective.values(),
            key=lambda o: (o.page_index, o.entity.start, o.entity.end, o.observation_id),
        )
    )
    render(
        context.machine, result, texts
    )  # Unresolved machine conflicts still block the whole job.
    return result


class CorrectionService:
    def __init__(self, host: CorrectionHost):
        self.host = host
        self.review = PrivateReviewBridge(host)

    def _authorize(self, credential, review_ref, scope):
        binding = self.review._binding(credential, review_ref)
        identity = self.review.authorization.identity(credential)
        if (
            self.host.current_acl(
                identity,
                "anonymization:correction:" + scope,
                binding.sensitivity,
                binding.environment_id,
            )
            is not True
        ):
            raise ValueError()
        return identity, binding

    def _context(self, credential, review_ref, scope):
        identity, binding = self._authorize(credential, review_ref, scope)
        material = self.review.open(credential, review_ref)
        context = CorrectionContext.model_validate(self.host.load_correction_context(review_ref))
        if context.material != material or self._authorize(credential, review_ref, scope) != (
            identity,
            binding,
        ):
            raise ValueError()
        return identity, context

    def inspect(self, credential, review_ref):
        try:
            return self._context(credential, review_ref, "inspect")[1]
        except Exception:
            raise SafeError("private_correction_denied") from None

    def _texts(self, credential, context):
        texts = {
            s.page_index: self.review.source_page(
                credential, context.material.binding.review_ref, s.page_index
            ).text
            for s in context.machine.sources
        }
        # Validate every captured/effective source slice, including observations
        # the proposed correction intends to supersede. No corrupt evidence repair.
        for observation in context.machine.observations:
            expected = commitment(
                dict(
                    evidence=context.machine.evidence_digest,
                    config=context.machine.provenance.configuration_digest,
                    page=observation.page_index,
                    entity=observation.entity.model_dump(mode="json"),
                )
            )
            if observation.observation_id != expected:
                raise ValueError()
        for observations in (context.machine.observations, context.effective):
            for source in context.machine.sources:
                validate_entities(
                    texts[source.page_index],
                    [o.entity for o in observations if o.page_index == source.page_index],
                )
            if any(o.page_index not in texts for o in observations):
                raise ValueError()
        try:
            original = render(context.machine, context.machine.observations, texts)
        except SafeError as exc:
            if (
                exc.code != "ambiguous_entity_overlap"
                or context.machine.machine_candidate is not None
            ):
                raise
        else:
            if (
                context.machine.machine_candidate is None
                or original != context.machine.machine_candidate.pages
            ):
                raise ValueError()
        candidate = context.material.candidate
        if (
            candidate is not None
            and render(context.machine, context.effective, texts) != candidate.pages
        ):
            raise ValueError()
        return texts

    def bind(self, credential, review_ref, page_index, start, end):
        try:
            _, context = self._context(credential, review_ref, "submit")
            text = self.review.source_page(credential, review_ref, page_index).text
            value = span_binding(context, page_index, start, end, text)
            self._authorize(credential, review_ref, "submit")
            return value
        except Exception:
            raise SafeError("private_correction_denied") from None

    def propose(self, credential, review_ref, edits):
        try:
            identity, context = self._context(credential, review_ref, "submit")
            if type(edits) not in (tuple, list) or not 1 <= len(edits) <= 1000:
                raise ValueError()
            edits = tuple(CorrectionEdit.model_validate(e) for e in edits)
            now = int(time.time())
            proposal = CorrectionProposal(
                correction_id=uuid.uuid4().hex,
                review_ref=review_ref,
                author_subject=identity.subject,
                evidence_digest=context.machine.evidence_digest,
                context_digest=commitment(context),
                machine_snapshot_digest=commitment(context.machine),
                prior_candidate_digest=context.material.candidate_digest,
                provenance=context.machine.provenance,
                edits=edits,
                created_at=now,
                expires_at=min(now + 900, context.material.binding.expires_at),
            )
            texts = self._texts(credential, context)
            apply_edits(context, proposal, texts)
            if self._context(credential, review_ref, "submit") != (identity, context):
                raise ValueError()
            if self.host.store_correction(credential, proposal, commitment(context)) is not True:
                raise ValueError()
            stored = StoredProposal.model_validate(
                self.host.load_correction(proposal.correction_id)
            )
            if (
                stored.proposal != proposal
                or stored.revoked
                or stored.published_candidate_digest is not None
            ):
                raise ValueError()
            return proposal
        except Exception:
            raise SafeError("private_correction_denied") from None

    def inspect_proposal(self, credential, correction_id):
        try:
            if type(correction_id) is not str or not re.fullmatch(r"[a-f0-9]{32}", correction_id):
                raise ValueError()
            # Authenticate first; the host must authorize this opaque-ID lookup
            # against its record binding before returning any proposal data.
            self.review.authorization.identity(credential)
            reference = self.host.correction_binding(correction_id)
            _, context = self._context(credential, reference, "inspect")
            record = StoredProposal.model_validate(self.host.load_correction(correction_id))
            if (
                record.proposal.correction_id != correction_id
                or record.proposal.review_ref != reference
            ):
                raise ValueError()
            self._authorize(credential, reference, "inspect")
            return CorrectionReview(context=context, record=record)
        except Exception:
            raise SafeError("private_correction_denied") from None

    def apply(self, credential, correction_id):
        try:
            view = self.inspect_proposal(credential, correction_id)
            proposal = view.record.proposal
            identity, context = self._context(credential, proposal.review_ref, "apply")
            now = int(time.time())
            if (
                view.record.revoked
                or view.record.published_candidate_digest is not None
                or identity.subject != proposal.author_subject
                or not proposal.created_at <= now < proposal.expires_at
            ):
                raise ValueError()
            texts = self._texts(credential, context)
            effective = apply_edits(context, proposal, texts)
            pages = render(context.machine, effective, texts)
            previous = context.material.candidate
            authors = (
                previous.correction.author_subject_digests
                if type(previous) is CorrectedCandidate
                else ()
            )
            authors = tuple(sorted(set(authors + (author_digest(identity.subject),))))
            correction = CorrectionProvenance(
                correction_id=proposal.correction_id,
                proposal_digest=commitment(proposal),
                original_evidence_digest=context.machine.evidence_digest,
                machine_snapshot_digest=commitment(context.machine),
                source_references_digest=commitment(
                    [s.model_dump(mode="json") for s in context.machine.sources]
                ),
                prior_candidate_digest=context.material.candidate_digest,
                prior_state_digest=state_digest(context.machine, context.effective),
                effective_state_digest=state_digest(context.machine, effective),
                author_subject_digests=authors,
                edit_count=len(proposal.edits),
            )
            candidate = CorrectedCandidate(
                candidate_id=uuid.uuid4().hex,
                sensitivity=context.material.binding.sensitivity,
                pages=pages,
                model_revision=context.machine.provenance.model_revision,
                provenance=context.machine.provenance,
                correction=correction,
                review_reasons=("recognition_incomplete_possible",)
                + (
                    ("empty_prediction",)
                    if any(
                        not any(
                            o.page_index == source.page_index
                            and o.entity.source == "multilingual_ner"
                            for o in context.machine.observations
                        )
                        for source in context.machine.sources
                    )
                    else ()
                )
                + ("human_correction_requires_review",),
            )
            binding = context.material.binding.model_copy(
                update={"review_ref": "correction-" + uuid.uuid4().hex}
            )
            material = PrivateReviewMaterial(
                binding=binding,
                evidence_digest=context.machine.evidence_digest,
                sources=context.machine.sources,
                candidate=candidate,
                candidate_digest=digest(candidate),
                provenance=context.machine.provenance,
                blocking_reason=None,
            )
            new = CorrectionContext(material=material, machine=context.machine, effective=effective)
            if self._context(credential, proposal.review_ref, "apply") != (identity, context):
                raise ValueError()
            expected = PublicationReceipt(
                correction_id=proposal.correction_id,
                proposal_digest=commitment(proposal),
                candidate_digest=digest(candidate),
                context_digest=commitment(new),
                review_ref=binding.review_ref,
            )
            actual = PublicationReceipt.model_validate(
                self.host.publish_correction(credential, proposal, new, commitment(context))
            )
            if actual != expected:
                raise ValueError()
            return actual
        except Exception:
            raise SafeError("private_correction_denied") from None
