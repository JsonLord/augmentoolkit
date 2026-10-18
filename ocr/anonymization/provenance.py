"""Canonical configuration commitment; no raw terms/prompts in candidate metadata."""

import hashlib
import json
import math

from ocr.schemas import SafeError

from .normalization import LINE_WRAP
from .replacement import REPLACEMENT_POLICY_VERSION
from .schemas import LABELS, RecognitionProvenance
from .terminology import TERMINOLOGY_REVISION


def provenance(recognizer, terminology, status_policy, placeholders):
    try:
        prompts = dict(recognizer.prompts)
        threshold = recognizer.threshold
        if (
            set(prompts) != set(LABELS)
            or any(
                not isinstance(v, str) or not 1 <= len(v) <= 80 or not v.strip()
                for v in prompts.values()
            )
            or len(set(prompts.values())) != len(LABELS)
            or type(threshold) not in (float, int)
            or not math.isfinite(threshold)
            or not 0 < threshold < 1
        ):
            raise ValueError()
        replacements = {k: f"[{k}]" for k in LABELS} if placeholders is None else dict(placeholders)
        # replace() validates the complete placeholder syntax independently.
        payload = dict(
            schema_version="recognition-configuration/v1",
            model_revision=recognizer.revision,
            threshold=float(threshold),
            prompts=prompts,
            terminology=dict(version=TERMINOLOGY_REVISION, terms=sorted(terminology.terms)),
            status=status_policy.configuration(),
            replacement=dict(version=REPLACEMENT_POLICY_VERSION, placeholders=replacements),
            normalization=dict(
                line_wrap=LINE_WRAP.pattern,
                unicode_casefold=True,
                offset_mapping="source-codepoint-v1",
            ),
            contact_policy="contact-patterns-v1",
        )
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode()
        return RecognitionProvenance(
            model_revision=recognizer.revision,
            configuration_digest=hashlib.sha256(encoded).hexdigest(),
        )
    except Exception:
        raise SafeError("invalid_recognition_configuration") from None
