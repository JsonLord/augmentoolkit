"""Finite religious-status grammar and optional single-substitution OCR evidence.

Protection of text only: never attributes a belief to a person. OCR matching is
restricted to long unambiguous status words, not arbitrary identifiers or names.
"""

import re
from dataclasses import dataclass

from ocr.schemas import SafeError

from .normalization import dehyphenate
from .schemas import Entity
from .terminology import _word

STATUS_VERSION = "religious-status-v1"
STATUS_WORDS = ("konfessionslos", "konfessionsfrei", "religionslos", "nonreligious")
STATUS_PATTERNS = (
    r"keine[rmn]?[ \t]{1,4}(?:Religion|Konfession)",
    r"ohne[ \t]{1,4}(?:Religion|Konfession)",
    r"religiously[ \t]{1,4}(?:unaffiliated|uncommitted|non-affiliated)",
    r"(?:no|without)[ \t]{1,4}(?:religion|religious affiliation)",
)
OCR_CLASSES = (frozenset("il1"), frozenset("o0"))


@dataclass(frozen=True)
class StatusPolicy:
    ocr_enabled: bool = True
    ocr_terms: tuple[str, ...] = STATUS_WORDS

    def __post_init__(self):
        if type(self.ocr_enabled) is not bool or (
            type(self.ocr_terms) is not tuple
            or not 1 <= len(self.ocr_terms) <= 64
            or any(
                not isinstance(t, str) or not re.fullmatch(r"[a-z]{12,40}", t)
                for t in self.ocr_terms
            )
        ):
            raise SafeError("invalid_status_configuration")

    def configuration(self):
        return dict(
            version=STATUS_VERSION,
            regex_flags=["IGNORECASE"],
            ocr_ascii_only=True,
            word_boundaries="unicode-alnum-combining-underscore-v1",
            patterns=STATUS_PATTERNS,
            ocr_enabled=self.ocr_enabled,
            ocr_terms=sorted(set(self.ocr_terms)),
            ocr_min_length=12,
            ocr_max_substitutions=1,
            ocr_classes=[sorted(c) for c in OCR_CLASSES],
        )

    def detect(self, text):
        if not isinstance(text, str) or len(text) > 2_000_000:
            raise SafeError("anonymization_text_limit")
        view, positions = dehyphenate(text)
        found = {}

        def add(left, right, ocr=False):
            if (left and _word(view[left - 1])) or (right < len(view) and _word(view[right])):
                return
            start, end = positions[left], positions[right - 1] + 1
            normalization = "identity" if view == text else "dehyphenation-v1"
            if ocr:
                normalization = "status-ocr-v1" if view == text else "dehyphenation-status-ocr-v1"
            found[(start, end)] = Entity(
                start=start,
                end=end,
                label="RELIGION",
                text=text[start:end],
                source="deterministic",
                model_revision=STATUS_VERSION,
                normalization=normalization,
            )
            if len(found) > 100_000:
                raise SafeError("invalid_entity_spans")

        for pattern in STATUS_PATTERNS:
            for match in re.finditer(pattern, view, re.IGNORECASE):
                add(match.start(), match.end())
        if self.ocr_enabled:
            for match in re.finditer(r"[A-Za-z01]{12,40}", view):
                word = match[0].lower()
                for term in self.ocr_terms:
                    if len(word) != len(term):
                        continue
                    differences = [(a, b) for a, b in zip(word, term, strict=True) if a != b]
                    if len(differences) == 1 and any(
                        set(differences[0]) <= group for group in OCR_CLASSES
                    ):
                        add(match.start(), match.end(), ocr=True)
                        break
        return list(found.values())
