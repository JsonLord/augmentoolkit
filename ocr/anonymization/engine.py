"""Trusted local recognition and all-or-nothing OCR handoff."""

import uuid

from ocr.schemas import OCRResult, SafeError

from .detectors import detect
from .model import MODEL_REVISION
from .replacement import replace, validate_entities
from .schemas import LABELS, CandidatePage, ReleaseCandidate
from .terminology import DEFAULT_TERMS, ReligiousTerminology


class AnonymizationEngine:
    def __init__(self, recognizer, *, placeholders=None, religion_terms=DEFAULT_TERMS):
        self.recognizer = recognizer
        self.placeholders = placeholders
        self.terminology = ReligiousTerminology(religion_terms)

    def transform(self, text):
        try:
            if set(self.recognizer.categories) != set(LABELS):
                raise SafeError("unsupported_required_categories")
            # Only local implementations are accepted. No remote recognizer transport exists.
            if self.recognizer.local is not True:
                raise SafeError("no_authorized_destination")
            if self.recognizer.revision != MODEL_REVISION:
                raise SafeError("anonymization_model_unavailable")
            if not isinstance(text, str) or len(text) > 2_000_000:
                raise SafeError("anonymization_text_limit")
            predictions = self.recognizer.recognize(text)
            if not isinstance(predictions, (list, tuple)):
                raise SafeError("invalid_entity_spans")
            predictions = validate_entities(text, predictions)
            if any(
                p.source != "multilingual_ner" or p.model_revision != MODEL_REVISION
                for p in predictions
            ):
                raise SafeError("invalid_entity_provenance")
            output, tags = replace(
                text, predictions + detect(text, terminology=self.terminology), self.placeholders
            )
            return output, tags, not predictions
        except SafeError as exc:
            allowed = {
                "unsupported_required_categories",
                "no_authorized_destination",
                "invalid_entity_spans",
                "invalid_entity_provenance",
                "ambiguous_entity_overlap",
                "anonymization_model_unavailable",
                "anonymization_text_limit",
                "anonymization_token_limit",
                "anonymization_inference_failed",
            }
            raise SafeError(exc.code if exc.code in allowed else "anonymization_failed") from None
        except Exception:
            raise SafeError("anonymization_failed") from None

    def handoff(self, result: OCRResult) -> ReleaseCandidate:
        try:
            if not isinstance(result, OCRResult):
                raise SafeError("invalid_ocr_evidence")
            if (
                result.status != "completed"
                or result.failures
                or not result.pages
                or result.page_count != len(result.pages)
                or result.page_count > 100
                or sum(len(p.text) for p in result.pages) > 2_000_000
            ):
                raise SafeError("incomplete_ocr_document")
            indexes = [p.page_index for p in result.pages]
            if indexes not in (
                list(range(result.page_count)),
                list(range(1, result.page_count + 1)),
            ):
                raise SafeError("incomplete_ocr_document")
            # Sensitivity mismatches are rejected, never silently downgraded.
            if any(p.sensitivity != result.sensitivity for p in result.pages):
                raise SafeError("classification_mismatch")
            pages, empty = [], False
            for p in result.pages:
                text, tags, no_predictions = self.transform(p.text)
                empty |= no_predictions
                pages.append(
                    CandidatePage(
                        page_index=p.page_index,
                        anonymized_text=text,
                        tags=tags,
                        source=p.source,
                        original_sensitivity=p.sensitivity,
                    )
                )
            reasons = ("recognition_incomplete_possible",) + (
                ("empty_prediction",) if empty else ()
            )
            # Rebuild exclusively from allowlisted fields; Markdown, blocks, alternatives,
            # engine/provider strings, page IDs and job IDs stay with classified evidence.
            return ReleaseCandidate(
                candidate_id=uuid.uuid4().hex,
                sensitivity=result.sensitivity,
                pages=tuple(pages),
                model_revision=self.recognizer.revision,
                review_reasons=reasons,
            )
        except SafeError:
            raise
        except Exception:
            raise SafeError("anonymization_failed") from None
