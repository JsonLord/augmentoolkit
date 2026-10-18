"""Deterministic source-coordinate replacement; no text normalization."""

import re

from pydantic import ValidationError

from ocr.schemas import SafeError

from .schemas import LABELS, Entity, Replacement


def validate_entities(text, predictions):
    try:
        if not isinstance(text, str) or not isinstance(predictions, (list, tuple)):
            raise ValueError()
        if len(text) > 2_000_000 or len(predictions) > 100_000:
            raise ValueError()
        entities = [Entity.model_validate(p) for p in predictions]
        for p in entities:
            if not 0 <= p.start < p.end <= len(text) or text[p.start : p.end] != p.text:
                raise ValueError()
        return entities
    except (ValidationError, ValueError, TypeError):
        raise SafeError("invalid_entity_spans") from None


def resolve_overlaps(entities):
    """Deduplicate, prefer enclosing spans; reject crossing spans/conflicting labels.

    Containment preserves coverage. Picking one crossing span could expose the
    other span's tail, so ambiguity blocks the entire document instead.
    """
    chosen = []
    for p in sorted(entities, key=lambda p: (p.start, -p.end, p.label)):
        if chosen and p.start < chosen[-1].end:
            previous = chosen[-1]
            if p.start == previous.start and p.end == previous.end:
                if p.label != previous.label:
                    raise SafeError("ambiguous_entity_overlap")
                # Prefer deterministic evidence; never fabricate a confidence.
                if p.source == "deterministic":
                    chosen[-1] = p
            elif p.end > previous.end:
                raise SafeError("ambiguous_entity_overlap")
        else:
            chosen.append(p)
    return chosen


def replace(text, predictions, placeholders=None):
    placeholders = placeholders if placeholders is not None else {k: f"[{k}]" for k in LABELS}
    if (
        not isinstance(placeholders, dict)
        or set(placeholders) != set(LABELS)
        or any(
            not isinstance(v, str) or not re.fullmatch(r"\[[A-Z_]{1,40}\]", v)
            for v in placeholders.values()
        )
    ):
        raise SafeError("unsupported_required_categories")
    entities = resolve_overlaps(validate_entities(text, predictions))
    chunks, tags, cursor = [], [], 0
    for p in entities:
        placeholder = placeholders[p.label]
        chunks.extend((text[cursor : p.start], placeholder))
        tags.append(
            Replacement(
                **p.model_dump(exclude={"text"}),
                placeholder=placeholder,
            )
        )
        cursor = p.end
    chunks.append(text[cursor:])
    return "".join(chunks), tuple(tags)
