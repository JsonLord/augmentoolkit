# Anonymization Qualification Report

## Mission Objective
Investigate and test the existing anonymization implementation before integrating it into the OCR workflow.

## 1. Implementation Audit
- **Recognizers Available:** Presidio (Analyzer/Anonymizer) is supported but optional. The code defaults to `nltk` (using `word_tokenize`, `pos_tag`, `ne_chunk`).
- **Entity Categories Supported:** Only `PERSON` and `LOCATION` are supported explicitly by both NLTK and Presidio configurations in `anonymize_text`. Organizations, Emails, Phone Numbers, and Religious Affiliations are **not** supported.
- **Languages:** English (`en`) is hardcoded in Presidio. German support is virtually non-existent, leading to substantial classification errors (e.g., mislabeling German words as English entities or completely missing them).
- **Span Replacement:** When it does detect an entity, the span replacement works reasonably well, but the NLTK logic sometimes joins punctuation incorrectly or misses multi-word entities.
- **Fail-Open Security Flaw (Baseline Defect):** If an exception occurs in `anonymize_text_nltk` (e.g., during tokenization or chunking), it catches the exception and returns the *original, unredacted text*. This means a recognition failure leaks the original sensitive content.
- **Integration with OCR:** The `ocr/handoff.py` script provides a secure bridge (`anonymization_handoff`) that properly returns the result as `release_review_required=True` and `anonymous=False`.

## 2. Evaluation Metrics (Span-Aware Synthetic Dataset)
A synthetic dataset of 40 text cases was tested. Because the current `anonymize_text` implementation does not yield character offsets (spans), entity-level Precision, Recall, and F1 metrics are explicitly marked as **UNAVAILABLE**.

Instead, we validated the gold-standard test spans and measured the exact document match rate alongside the required canary leakage.

```text
--- Evaluation Metrics ---
PERSON: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 5}
CITY: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 3}
RELIGION: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 5}
EMAIL: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 2}
PHONE: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 1}
ORGANIZATION: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 2}
document_exact_match_rate: 0.175
avg_runtime_ms: 840.4
```

- **Document Exact Match Rate**: Only 17.5% of the test documents were redacted exactly as specified in the gold standard.
- **Canary Leakage**: Every required entity category saw significant canary leakage.
- **Unsupported Categories**: `RELIGION`, `EMAIL`, `PHONE`, and `ORGANIZATION` are unhandled by the NLTK/Presidio implementations. This leads to large numbers of unredacted canary leaks.

## 3. OCR-to-Anonymization Integration
The integration test (`test_ocr_to_anonymization_integration`) is marked as opt-in to avoid injecting heavyweight ML dependencies into the default test run environment.
The mock testing validates the handoff interface accurately, confirming:
- The handoff correctly assigns `release_review_required=True`
- The handoff correctly assigns `anonymous=False`
- The original evidence schema is correctly preserved.
- (Acceptance test logic added): It accurately rejects malformed inputs and triggers `SafeError` on tagger exception.

## 4. Final Decision
**Decision:** `NO-GO`

**Reasons:**
1. **Fail-Open Behavior:** The existing NLTK baseline implementation returns the original text on failure. This is a critical security vulnerability.
2. **Missing Entity Support:** Essential categories (`[RELIGION]`, `[EMAIL]`, `[PHONE]`, `[ORGANIZATION]`) are completely unhandled, immediately leading to compliance failures (canary leakage).
3. **Missing Multilingual Support:** The hardcoded English Presidio configuration and NLTK fallback severely underperform on German text.

**Recommendation:**
The next implementation gate needs to:
- Introduce a multilingual GLiNER-based recognizer (or similar robust multilingual model) to properly handle German and custom entity tags like `RELIGION` without downloading arbitrary corpora.
- The recognizer must return explicit spans to calculate accurate P/R/F1.
- Implement explicit catch blocks inside the tagger wrappers that fail closed (throw `Exception`) ensuring `ocr/handoff.py` correctly converts it to a `SafeError`.
