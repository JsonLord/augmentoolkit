# Anonymization Qualification Report

## Mission Objective
Investigate and test the existing anonymization implementation before integrating it into the OCR workflow.

## 1. Implementation Audit
- **Recognizers Available:** Presidio (Analyzer/Anonymizer) is supported but optional. The code defaults to `nltk` (using `word_tokenize`, `pos_tag`, `ne_chunk`).
- **Entity Categories Supported:** Only `PERSON` and `LOCATION` are supported explicitly by both NLTK and Presidio configurations in `anonymize_text`. Organizations, Emails, Phone Numbers, and Religious Affiliations are **not** supported.
- **Languages:** English (`en`) is hardcoded in Presidio. German support is virtually non-existent, leading to substantial classification errors (e.g., mislabeling German words as English entities or completely missing them).
- **Span Replacement:** When it does detect an entity, the span replacement works reasonably well, but the NLTK logic sometimes joins punctuation incorrectly or misses multi-word entities.
- **Fail-Open Security Flaw:** If an exception occurs in `anonymize_text_nltk` (e.g., during tokenization or chunking), it catches the exception and returns the *original, unredacted text*. This means a recognition failure leaks the original sensitive content.
- **Integration with OCR:** The `ocr/handoff.py` script provides a secure bridge (`anonymization_handoff`) that properly returns the result as `release_review_required=True`. However, `augmentoolkit_extract` never explicitly invokes the anonymization step by default; it only returns the classified evidence.

## 2. Evaluation Metrics (Synthetic Dataset)
A synthetic dataset of 40 text cases was tested.

```text
--- Evaluation Metrics ---
PERSON: {'precision': 0.6, 'recall': 0.9, 'f1': 0.7200000000000001, 'false_positives': 12, 'false_negatives': 2}
CITY: {'precision': 0.6666666666666666, 'recall': 0.7058823529411765, 'f1': 0.6857142857142857, 'false_positives': 6, 'false_negatives': 5}
RELIGION: {'precision': 0, 'recall': 0.0, 'f1': 0, 'false_positives': 0, 'false_negatives': 6}
unredacted_canaries: 9
avg_runtime_ms: 1038.0
```

- **English PERSON**: High precision, moderate recall (misses some edge cases).
- **German PERSON**: Very low recall (e.g., "Lina Wolkenstein" detected as CITY).
- **CITY (Location)**: Moderate for English, very poor for German.
- **RELIGION**: 0% recall (completely unsupported).
- **Unredacted Canaries**: 9. Leaked the canary data.

## 3. OCR-to-Anonymization
When feeding the string `"Lina Wolkenstein wohnt in Berlin. Ihre Religionszugehoerigkeit ist evangelisch."` through the OCR (tesseract) and then the anonymization pipeline, the resulting output was:
`"[CITY] [PERSON] wohntin [PERSON]. [PERSON]"`
- The pipeline completely scrambled the entity types (labeling parts of 'Lina Wolkenstein' as CITY) and lost 'Berlin' to another incorrect token classification.
- 'Religionszugehoerigkeit' was completely ignored.

## 4. Final Decision
**Decision:** `NO-GO`

**Reasons:**
1. **Fail-Open Behavior:** The existing NLTK implementation returns the original text on failure. This is a critical security vulnerability.
2. **Missing Language Support:** The hardcoded English Presidio configuration and NLTK fallback severely underperform on German text.
3. **Missing Entity Support:** Essential categories like `[RELIGION]` are completely unhandled.

**Recommendation:**
The next implementation gate needs to:
- Introduce a multilingual GLiNER-based recognizer (or similar robust multilingual model) to properly handle German and custom entity tags like `RELIGION`.
- Change `anonymize_text` to fail-closed (raise an exception instead of returning the original text) to ensure `ocr/handoff.py` can properly catch the exception and halt the release of unredacted text.
