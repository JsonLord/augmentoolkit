"""Authorized in-process review views. No HTTP endpoint, exporter or IdP.

Only a real host adapter can attest bindings, ACLs and immutable source storage.
Views remain classified; showing a view or source page never completes review or
creates release permission. Original pages must be reviewed, not only detected spans.
"""

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ocr.schemas import SafeError

from .host_adapter import HostAuthorizationBridge, HostSecurityAdapter
from .release_policy import digest
from .schemas import (
    CorrectedCandidate,
    LegacyReleaseCandidate,
    RecognitionProvenance,
    ReleaseCandidate,
    StrictModel,
)


class ReviewBinding(StrictModel):
    review_ref: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    environment_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    sensitivity: Literal["D0", "D1", "D2"]
    expires_at: int
    revoked: bool


class SourcePageReference(StrictModel):
    evidence_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_index: int = Field(ge=0)
    sensitivity: Literal["D0", "D1", "D2"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    characters: int = Field(ge=0, le=2_000_000)


class PrivateReviewMaterial(StrictModel):
    binding: ReviewBinding
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    sources: tuple[SourcePageReference, ...] = Field(min_length=1, max_length=100, repr=False)
    candidate: CorrectedCandidate | ReleaseCandidate | LegacyReleaseCandidate | None = Field(
        repr=False
    )
    candidate_digest: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    provenance: RecognitionProvenance | None
    blocking_reason: (
        Literal["conflicting_spans", "model_runtime_failure", "other_incomplete"] | None
    )
    anonymous: Literal[False] = False
    automated_release_eligible: Literal[False] = False
    release_review_required: Literal[True] = True

    @model_validator(mode="after")
    def consistent_record(self):
        sources = {p.page_index: p for p in self.sources}
        if (
            len(sources) != len(self.sources)
            or sum(p.characters for p in self.sources) > 2_000_000
            or any(p.sensitivity != self.binding.sensitivity for p in self.sources)
        ):
            raise ValueError("invalid_private_review_material")
        if self.candidate is None:
            if (
                self.candidate_digest is not None
                or self.blocking_reason is None
                or self.provenance is None
            ):
                raise ValueError("invalid_private_review_material")
        else:
            if (
                self.candidate_digest != digest(self.candidate)
                or self.blocking_reason is not None
                or self.candidate.sensitivity != self.binding.sensitivity
                or len(self.sources) != len(self.candidate.pages)
            ):
                raise ValueError("invalid_private_review_material")
            if type(self.candidate) in (ReleaseCandidate, CorrectedCandidate):
                if self.provenance != self.candidate.provenance:
                    raise ValueError("invalid_private_review_material")
            elif self.provenance is not None:
                raise ValueError("legacy_provenance_unavailable")
            for page in self.candidate.pages:
                if page.page_index not in sources or any(
                    not 0 <= tag.start < tag.end <= sources[page.page_index].characters
                    for tag in page.tags
                ):
                    raise ValueError("invalid_private_review_material")
        return self

    @property
    def categories(self):
        return (
            tuple(sorted({t.label for p in self.candidate.pages for t in p.tags}))
            if self.candidate
            else ()
        )

    @property
    def review_flags(self):
        # No fixture/gold knowledge or model success ever removes these flags.
        ocr = self.candidate is not None and any(
            "status-ocr" in t.normalization for p in self.candidate.pages for t in p.tags
        )
        return (
            (
                "recognition_incomplete_possible",
                "model_scores_uncalibrated",
                "review_complete_source_pages",
            )
            + (("processing_blocked",) if self.candidate is None else self.candidate.review_reasons)
            + (
                ("legacy_configuration_unknown",)
                if type(self.candidate) is LegacyReleaseCandidate
                else ()
            )
        ) + (("ocr_status_normalization",) if ocr else ())


@dataclass(frozen=True)
class ClassifiedPageView:
    sensitivity: Literal["D0", "D1", "D2"]
    page_index: int
    text: str = field(repr=False)


class PrivateReviewHost(HostSecurityAdapter, Protocol):
    def review_binding(self, review_ref: str) -> ReviewBinding: ...
    def private_environment(self, environment_id: str) -> bool: ...
    def load_review_material(self, review_ref: str) -> PrivateReviewMaterial: ...
    def read_classified_page(self, evidence_ref: str, page_index: int) -> bytes: ...


class PrivateReviewBridge:
    def __init__(self, host: PrivateReviewHost):
        self.host = host
        self.authorization = HostAuthorizationBridge(host)

    def _binding(self, credential, review_ref):
        if type(review_ref) is not str or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", review_ref):
            raise ValueError()
        identity = self.authorization.identity(credential)
        binding = ReviewBinding.model_validate(self.host.review_binding(review_ref))
        if (
            binding.review_ref != review_ref
            or binding.revoked
            or binding.expires_at <= time.time()
            or self.host.private_environment(binding.environment_id) is not True
            or self.host.current_acl(
                identity, "anonymization:review", binding.sensitivity, binding.environment_id
            )
            is not True
        ):
            raise ValueError()
        return binding

    def open(self, credential, review_ref):
        try:
            binding = self._binding(credential, review_ref)
            material = PrivateReviewMaterial.model_validate(
                self.host.load_review_material(review_ref)
            )
            if (
                material.candidate is not None
                and self.host.candidate_exists(material.candidate_digest) is not True
            ):
                raise ValueError()
            if material.binding != binding or self._binding(credential, review_ref) != binding:
                raise ValueError()
            return material
        except Exception:
            raise SafeError("private_review_access_denied") from None

    def source_page(self, credential, review_ref, page_index):
        try:
            if type(page_index) is not int:
                raise ValueError()
            material = self.open(credential, review_ref)
            source = next(p for p in material.sources if p.page_index == page_index)
            data = self.host.read_classified_page(source.evidence_ref, page_index)
            if (
                not isinstance(data, bytes)
                or len(data) > 8 * 1024 * 1024
                or hashlib.sha256(data).hexdigest() != source.sha256
            ):
                raise ValueError()
            text = data.decode("utf-8", errors="strict")
            if len(text) != source.characters or self.open(credential, review_ref) != material:
                raise ValueError()
            return ClassifiedPageView(material.binding.sensitivity, page_index, text)
        except Exception:
            raise SafeError("private_review_access_denied") from None
