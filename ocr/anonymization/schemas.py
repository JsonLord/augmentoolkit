"""Classified detection evidence and allowlisted candidate data are separate types."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Label = Literal["PERSON", "CITY", "RELIGION", "EMAIL", "PHONE", "ORGANIZATION"]
LABELS = ("PERSON", "CITY", "RELIGION", "EMAIL", "PHONE", "ORGANIZATION")


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class Entity(StrictModel):
    """Private evidence only: never serialize this type into a release candidate."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    label: Label
    text: str
    score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    source: Literal["multilingual_ner", "deterministic"]
    model_revision: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")
    normalization: Literal[
        "identity", "dehyphenation-v1", "status-ocr-v1", "dehyphenation-status-ocr-v1"
    ] = "identity"


class Replacement(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    label: Label
    placeholder: str = Field(pattern=r"^\[[A-Z_]{1,40}\]$")
    score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    source: Literal["multilingual_ner", "deterministic", "human_correction"]
    model_revision: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")
    normalization: Literal[
        "identity", "dehyphenation-v1", "status-ocr-v1", "dehyphenation-status-ocr-v1"
    ] = "identity"

    @model_validator(mode="after")
    def original_source_bounds(self):
        if self.start >= self.end:
            raise ValueError("invalid_replacement_bounds")
        # The source length is not the anonymized text length. Its upper bound
        # is checked by PrivateReviewMaterial when original references exist.
        return self


class CandidatePage(StrictModel):
    page_index: int = Field(ge=0)
    anonymized_text: str
    tags: tuple[Replacement, ...]
    source: Literal["embedded_text", "cpu_ocr", "alias_ocr"]
    original_sensitivity: Literal["D0", "D1", "D2"]


class LegacyReleaseCandidate(StrictModel):
    schema_version: Literal["ocr-anonymization-candidate/v1"] = "ocr-anonymization-candidate/v1"
    candidate_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sensitivity: Literal["D0", "D1", "D2"]
    pages: tuple[CandidatePage, ...] = Field(min_length=1)
    model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    anonymous: Literal[False] = False
    release_review_required: Literal[True] = True
    release_approved: Literal[False] = False
    automated_release_eligible: Literal[False] = False
    review_reasons: tuple[Literal["recognition_incomplete_possible", "empty_prediction"], ...]

    @model_validator(mode="after")
    def document_integrity(self):
        indexes = [page.page_index for page in self.pages]
        if len(set(indexes)) != len(indexes) or any(
            page.original_sensitivity != self.sensitivity for page in self.pages
        ):
            raise ValueError("invalid_candidate_document")
        # No new contiguous numbering rule for valid legacy documents. Exact
        # source coverage is checked where immutable source references exist.
        return self

    @model_validator(mode="after")
    def legacy_normalization(self):
        if self.schema_version in (
            "ocr-anonymization-candidate/v1",
            "ocr-anonymization-candidate/v2",
        ) and any(t.source == "human_correction" for p in self.pages for t in p.tags):
            raise ValueError("unsupported_human_correction_version")
        if self.schema_version == "ocr-anonymization-candidate/v1" and any(
            tag.normalization not in ("identity", "dehyphenation-v1")
            for page in self.pages
            for tag in page.tags
        ):
            raise ValueError("unsupported_legacy_normalization")
        return self


class RecognitionProvenance(StrictModel):
    schema_version: Literal["recognition-provenance/v1"] = "recognition-provenance/v1"
    model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    configuration_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    terminology_version: Literal["religious-terminology-v1"] = "religious-terminology-v1"
    status_version: Literal["religious-status-v1"] = "religious-status-v1"
    replacement_version: Literal["enclosing-spans-failclosed-v1"] = "enclosing-spans-failclosed-v1"
    normalization_version: Literal["source-codepoint-v1"] = "source-codepoint-v1"


class ReleaseCandidate(LegacyReleaseCandidate):
    schema_version: Literal["ocr-anonymization-candidate/v2"] = "ocr-anonymization-candidate/v2"
    provenance: RecognitionProvenance

    @model_validator(mode="after")
    def consistent_provenance(self):
        if self.model_revision != self.provenance.model_revision:
            raise ValueError("candidate_provenance_mismatch")
        return self


class CorrectionProvenance(StrictModel):
    schema_version: Literal["ocr-correction-provenance/v1"] = "ocr-correction-provenance/v1"
    policy_version: Literal["reviewed-corrections-v1"] = "reviewed-corrections-v1"
    correction_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    machine_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_references_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    prior_candidate_digest: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    prior_state_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective_state_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    author_subject_digests: tuple[str, ...] = Field(min_length=1, max_length=1000)
    edit_count: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def valid_authors(self):
        import re

        if len(set(self.author_subject_digests)) != len(self.author_subject_digests) or any(
            not re.fullmatch(r"[a-f0-9]{64}", s) for s in self.author_subject_digests
        ):
            raise ValueError("invalid_correction_authors")
        return self


class CorrectedCandidate(ReleaseCandidate):
    schema_version: Literal["ocr-anonymization-candidate/v3"] = "ocr-anonymization-candidate/v3"
    correction: CorrectionProvenance
    review_reasons: tuple[
        Literal[
            "recognition_incomplete_possible",
            "empty_prediction",
            "human_correction_requires_review",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def reviewed_correction_only(self):
        if "human_correction_requires_review" not in self.review_reasons or any(
            t.source == "human_correction"
            and (
                t.score is not None
                or t.model_revision != "reviewed-corrections-v1"
                or t.normalization != "identity"
            )
            for p in self.pages
            for t in p.tags
        ):
            raise ValueError("invalid_corrected_candidate")
        return self


class Destination(StrictModel):
    """Operator-controlled destination registry entry, never a caller supplied URL."""

    destination_id: str = Field(pattern=r"^[a-zA-Z0-9:_-]{1,128}$")
    destination_class: Literal["private", "public"]
    allowed_sensitivities: tuple[Literal["D0", "D1", "D2"], ...]


class VerifiedReview(StrictModel):
    """Attestation returned by the host application's authenticated review store."""

    reviewer_subject: str = Field(pattern=r"^[a-zA-Z0-9:_-]{1,128}$")
    review_id: str = Field(pattern=r"^[a-zA-Z0-9:_-]{1,128}$")
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    sensitivity: Literal["D0", "D1", "D2"]
    destination_id: str = Field(pattern=r"^[a-zA-Z0-9:_-]{1,128}$")
    completed_at: int = Field(ge=0)


class Approval(StrictModel):
    schema_version: Literal["ocr-review-approval/v2"] = "ocr-review-approval/v2"
    approval_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review: VerifiedReview
    destination: Destination
    format: Literal["json", "text"]
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")
