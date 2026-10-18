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
    start: int
    end: int
    label: Label
    placeholder: str = Field(pattern=r"^\[[A-Z_]{1,40}\]$")
    score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    source: Literal["multilingual_ner", "deterministic"]
    model_revision: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")
    normalization: Literal[
        "identity", "dehyphenation-v1", "status-ocr-v1", "dehyphenation-status-ocr-v1"
    ] = "identity"


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
    pages: tuple[CandidatePage, ...]
    model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    anonymous: Literal[False] = False
    release_review_required: Literal[True] = True
    release_approved: Literal[False] = False
    automated_release_eligible: Literal[False] = False
    review_reasons: tuple[Literal["recognition_incomplete_possible", "empty_prediction"], ...]

    @model_validator(mode="after")
    def legacy_normalization(self):
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
