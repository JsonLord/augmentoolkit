"""Deterministic contact patterns, not an exhaustive identifier detector."""

import re

from .schemas import Entity
from .terminology import ReligiousTerminology

DEFAULT_TERMINOLOGY = ReligiousTerminology()

EMAIL = re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])")
PHONE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,5}\)[ .-]?)?\d{2,8}(?:[ ./-]\d{2,10}){1,4}(?!\w)"
)


def detect_contacts(text):
    found = []
    for label, pattern in (("EMAIL", EMAIL), ("PHONE", PHONE)):
        for match in pattern.finditer(text):
            if label == "PHONE" and not 7 <= sum(c.isdigit() for c in match[0]) <= 15:
                continue
            found.append(
                Entity(
                    start=match.start(),
                    end=match.end(),
                    label=label,
                    text=match[0],
                    source="deterministic",
                    model_revision="contact-patterns-v1",
                )
            )
    return found


def detect(text, *, terminology=DEFAULT_TERMINOLOGY):
    """Protect literal religious/status terms as well as contact identifiers."""
    return detect_contacts(text) + terminology.detect(text)
