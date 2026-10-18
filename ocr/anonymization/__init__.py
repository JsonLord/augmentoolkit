"""Explicit trusted-layer anonymization; no legacy fallback or network inference."""

from .candidate_io import parse_candidate
from .engine import AnonymizationEngine
from .release_policy import ReleaseAuthority
from .schemas import Entity, LegacyReleaseCandidate, ReleaseCandidate

__all__ = [
    "AnonymizationEngine",
    "Entity",
    "ReleaseAuthority",
    "ReleaseCandidate",
    "LegacyReleaseCandidate",
    "parse_candidate",
]
