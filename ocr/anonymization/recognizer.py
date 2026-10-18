"""Local native GLiNER runtime. Character offsets always refer to untouched text."""

import math
import re
import threading

from ocr.schemas import SafeError

from .model import MODEL_REVISION, verify_cache
from .normalization import dehyphenate
from .replacement import validate_entities
from .schemas import LABELS, Entity

DEFAULT_PROMPTS = {
    "PERSON": "person",
    # Privacy category includes geopolitical places, matching the existing
    # qualification taxonomy (cities, states and named geographic locations).
    "CITY": "location",
    "RELIGION": "religion",
    "EMAIL": "email address",
    "PHONE": "phone number",
    "ORGANIZATION": "organization",
}


class GLiNERRecognizer:
    local = True
    revision = MODEL_REVISION
    categories = LABELS

    def __init__(self, cache, *, device="cpu", threshold=0.5, prompts=None, threads=2):
        self._lock = threading.Lock()
        try:
            self.prompts = dict(DEFAULT_PROMPTS if prompts is None else prompts)
        except Exception:
            raise SafeError("unsupported_required_categories") from None
        if set(self.prompts) != set(LABELS) or (
            any(
                not isinstance(v, str) or not v.strip() or len(v) > 80
                for v in self.prompts.values()
            )
            or len(set(self.prompts.values())) != len(LABELS)
        ):
            raise SafeError("unsupported_required_categories")
        if (
            type(threshold) not in (int, float)
            or not math.isfinite(threshold)
            or not 0 < threshold < 1
        ):
            raise SafeError("invalid_recognizer_configuration")
        if device not in ("cpu", "cuda") or type(threads) is not int or not 1 <= threads <= 16:
            raise SafeError("invalid_recognizer_configuration")
        self.threshold = threshold
        try:
            path = verify_cache(cache)
            import torch
            from gliner import GLiNER

            torch.set_num_threads(threads)
            self.model = GLiNER.from_pretrained(
                str(path),
                local_files_only=True,
                map_location=device,
                _attn_implementation="eager",
                strict=True,
            )
            self.model.eval()
        except Exception:
            raise SafeError("anonymization_model_unavailable") from None

    def recognize(self, text):
        if not isinstance(text, str) or len(text) > 2_000_000:
            raise SafeError("anonymization_text_limit")
        normalized, positions = dehyphenate(text)
        predictions = self._recognize_view(normalized)
        if normalized == text:
            return predictions
        mapped = []
        for entity in predictions:
            start = positions[entity.start]
            end = positions[entity.end - 1] + 1
            mapped.append(
                entity.model_copy(
                    update={
                        "start": start,
                        "end": end,
                        "text": text[start:end],
                        "normalization": "dehyphenation-v1",
                    }
                )
            )
        return validate_entities(text, mapped)

    def _recognize_view(self, text):
        if not isinstance(text, str) or len(text) > 2_000_000:
            raise SafeError("anonymization_text_limit")
        # Overlapping windows cover all original characters without normalizing OCR noise.
        # Hard bounds prevent silent tokenizer/model truncation on unusual long tokens.
        words = list(re.finditer(r"\S+", text))
        if any(len(w[0]) > 256 for w in words):
            raise SafeError("anonymization_token_limit")
        entities = []
        reverse = {v: k for k, v in self.prompts.items()}
        try:
            import torch

            with self._lock, torch.inference_mode():
                for start in range(0, len(words), 96):
                    window = words[start : start + 128]
                    left, right = window[0].start(), window[-1].end()
                    chunk = text[left:right]
                    encoded = self.model.data_processor.transformer_tokenizer(chunk)["input_ids"]
                    if len(encoded) > 1536:
                        raise SafeError("anonymization_token_limit")
                    raw = self.model.predict_entities(
                        chunk,
                        list(self.prompts.values()),
                        threshold=self.threshold,
                        flat_ner=True,
                    )
                    if not isinstance(raw, list):
                        raise ValueError()
                    for item in raw:
                        if not isinstance(item, dict) or set(item) != {
                            "start",
                            "end",
                            "text",
                            "label",
                            "score",
                        }:
                            raise ValueError()
                        # Validate runtime output before adding the original window offset.
                        local = Entity(
                            start=item["start"],
                            end=item["end"],
                            text=item["text"],
                            label=reverse[item["label"]],
                            score=item["score"],
                            source="multilingual_ner",
                            model_revision=self.revision,
                        )
                        validate_entities(chunk, [local])
                        entities.append(
                            local.model_copy(
                                update={"start": local.start + left, "end": local.end + left}
                            )
                        )
            return validate_entities(text, entities)
        except SafeError:
            raise
        except Exception:
            raise SafeError("anonymization_inference_failed") from None
