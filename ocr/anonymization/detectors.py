"""Deterministic contact patterns, not an exhaustive identifier detector."""

import re

from .schemas import Entity

EMAIL = re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])")
PHONE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,5}\)[ .-]?)?\d{2,8}(?:[ ./-]\d{2,10}){1,4}(?!\w)"
)


def detect(text):
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
