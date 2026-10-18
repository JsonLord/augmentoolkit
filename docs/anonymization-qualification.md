> Current result: [Gate03d religious terminology qualification](anonymization-gate03d.md)
> preserves the failed Gate03c result, compares limited fixes, and records a FAILED
> new independent 160-case first holdout. Model recognition, real sensitive processing,
> private deployment and automated release remain NO-GO. Earlier sections are history.

# Anonymization Qualification Report

Current staging/sensitive-processing decision: **NO-GO**. The fresh 120-case
[Gate 03c holdout](anonymization-gate03c.md) has two unmasked religious-reference
canaries; real identity integration remains BLOCKED. Earlier findings below are
preserved as historical evidence.

## Mission Objective
Investigate and test the existing anonymization implementation before integrating it into the OCR workflow.

## 1. Implementation Audit
- **Recognizers Available:** Presidio (Analyzer/Anonymizer) is supported but optional. The code defaults to `nltk` (using `word_tokenize`, `pos_tag`, `ne_chunk`).
- **Entity Categories Supported:** Only `PERSON` and `LOCATION` are supported explicitly by both NLTK and Presidio configurations in `anonymize_text`. Organizations, Emails, Phone Numbers, and Religious Affiliations are **not** supported.
- **Languages:** English (`en`) is hardcoded in Presidio. German support is virtually non-existent, leading to substantial classification errors (e.g., mislabeling German words as English entities or completely missing them).
- **Span Replacement:** When it does detect an entity, the span replacement works reasonably well, but the NLTK logic sometimes joins punctuation incorrectly or misses multi-word entities.
- **Fail-Open Security Flaw (Baseline Defect):** If an exception occurs in `anonymize_text_nltk` (e.g., during tokenization or chunking), it catches the exception and returns the *original, unredacted text*. This means a recognition failure leaks the original sensitive content.
- **Integration with OCR:** The `ocr/handoff.py` script provides a secure bridge (`anonymization_handoff`) that properly returns the result as `release_review_required=True` and `anonymous=False`.

## 2. Evaluation Metrics (Document-Level Exact-Match Scoring)
A synthetic dataset of 40 text cases was tested. Because the current `anonymize_text` implementation does not yield character offsets (spans), entity-level Precision, Recall, and F1 metrics are explicitly marked as **UNAVAILABLE**.

*Note: The following metrics are published benchmark observations derived from local test runs using the baseline NLTK implementation. They are historical/unverified in standard CI, as the real model evaluation is marked as an opt-in test (`RUN_OPTIONAL_OCR_INTEGRATION=1`) to avoid arbitrary heavy ML dependencies in the default GitHub Actions run.*

```text
--- Benchmark Observations ---
PERSON: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 5}
CITY: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 3}
RELIGION: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 5}
EMAIL: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 2}
PHONE: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 1}
ORGANIZATION: {'precision': 'UNAVAILABLE', 'recall': 'UNAVAILABLE', 'f1': 'UNAVAILABLE', 'unredacted_canaries': 2}
document_exact_match_rate: 0.175
avg_runtime_ms: ~840.0
```

- **Document Exact Match Rate**: Only 17.5% of the test documents were redacted exactly as specified in the gold standard.
- **Canary Leakage**: Every required entity category saw significant canary leakage.
- **Unsupported Categories**: `RELIGION`, `EMAIL`, `PHONE`, and `ORGANIZATION` are unhandled by the NLTK/Presidio implementations. This leads to large numbers of unredacted canary leaks.

## 3. Testing Context and Integration
The independent verification tests executing globally in CI cover:
- Validation of all 40 synthetic gold-standard entity spans.
- Acceptance tests proving `anonymization_handoff` handles malformed outputs securely (`SafeError`) and correctly bounds metadata (`release_review_required=True`, `anonymous=False`).
- Baseline demonstration of the NLTK fail-open security flaw.

An opt-in integration test (`test_ocr_to_anonymization_integration`) simulates the OCR-to-anonymizer integration boundary. It demonstrates that the legacy baseline **fails to fully redact** required canaries like "Lina", "Wolkenstein", "Berlin", and "evangelisch".

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


---

## New span engine qualification — 2026-10-18

This section qualifies the new explicit implementation. All preceding legacy
findings and characterization tests are preserved unchanged; their NO-GO decision
still applies to the legacy engine. A test demonstrating its fail-open behavior
is not a passing acceptance test for security.

**Decision: PARTIAL for reviewed private integration; NO-GO for automated release.**
The boundary/replacement implementation passes its offline acceptance tests. The
actual pinned recognizer still leaves one required synthetic CITY canary unmasked.
The opt-in canary acceptance test fails deliberately without xfail or altered gold
labels. Every candidate remains ineligible for automatic release.

### Synchronization evidence

- Original checkout: `work`, `9fc91e6f92b4ae9cc902359fd56ef0011a10f9f1`.
- Prior review worktree: `review/pr-1-fixes`, `a8f5d7b22c6c5787621973aa5207311d2cc75dca`; uncommitted corrections preserved untouched.
- No pre-existing local anonymization implementation branch or commits existed.
- Fetched/verified integration branch: `jules-agent-0-deployment-9610133099829518547`, `281c84172281a2294227f15287a4aded8a7ea948`. GitHub confirms PR #2 merged at that SHA.
- Feature branch: `feat/ocr-anonymization-failclosed`, initial local HEAD `281c84172281a2294227f15287a4aded8a7ea948`. No rebase/conflicts were necessary; shared integration branch was not changed.
- Worktree: `/workspace/augmentoolkit-anonymization`. Final delivery SHA/PR/CI are in the delivery report; `git merge-base --is-ancestor origin/jules-agent-0-deployment-9610133099829518547 HEAD` verifies ancestry.

### Actual-model evaluation

Selected `VAGOsolutions/SauerkrautLM-GLiNER` revision
`ba967ee030b7e679dd5422cb16fcd1b379da38e1`, native local CPU runtime, threshold
0.5, two inference threads. Linux container CPU quota: four CPUs; memory ceiling
32 GiB. No GPU, external model endpoint, public worker or real private documents
were used. The actual recognition tests start from synthetic mocked OCRResult
objects; this task did not rerun image/PDF OCR benchmarks.

Raw reproducible aggregate evidence: [cpu-pinned.json](anonymization-evaluation/cpu-pinned.json).
Installation/runtime pins and exact commands: [integration](anonymization-integration.md).

The 40-case corpus is a faithful copy of merged qualification cases, with language
and noise annotations added. A separate 10-case validation set was written before
model evaluation and is not read by the runtime. No test entity names are hardcoded
in recognizers/detectors. Generic line-wrap handling was developed after the initial
evaluation; this is development feedback, not independent external validation.
Further held-out real-world evaluation is required to assess overfitting/domain shift.

Metrics below match exact **(start, end, label)** tuples. Wrong boundaries or labels
count as both a false positive and a false negative. NER-only metrics and combined
NER + deterministic detector metrics are separately recorded in JSON; their final
counts match on these small corpora. A canary fails if any original alphanumeric
character in its gold span remains uncovered, even if partial masking removed the
full literal string. Literal-string checks are also recorded separately.

#### Merged qualification corpus (40 cases)

| Entity | Precision | Recall | F1 | FP | FN | Unredacted canaries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PERSON | 0.818 | 0.900 | 0.857 | 4 | 2 | 0 |
| CITY | 1.000 | 0.824 | 0.903 | 0 | 3 | 1 |
| RELIGION | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| EMAIL | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| PHONE | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| ORGANIZATION | 0.250 | 0.500 | 0.333 | 3 | 1 | 0 |

Document exact-match rate: **82.5%**; blocked documents: 0.

| Group | Documents | Exact matches | Entity FN |
| --- | ---: | ---: | ---: |
| de | 10 | 8 | 1 |
| en | 29 | 24 | 5 |
| mixed | 1 | 1 | 0 |
| ocr_noise | 4 | 2 | 2 |

Warm latency median 89.4 ms, maximum 335.5 ms.

#### Separate validation corpus (10 cases)

| Entity | Precision | Recall | F1 | FP | FN | Unredacted canaries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PERSON | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| CITY | 1.000 | 0.800 | 0.889 | 0 | 1 | 0 |
| RELIGION | 0.667 | 1.000 | 0.800 | 1 | 0 | 0 |
| EMAIL | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| PHONE | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| ORGANIZATION | 0.500 | 1.000 | 0.667 | 2 | 0 | 0 |

Document exact-match rate: **70.0%**; blocked documents: 0.

| Group | Documents | Exact matches | Entity FN |
| --- | ---: | ---: | ---: |
| de | 5 | 3 | 1 |
| en | 5 | 4 | 0 |
| ocr_noise | 2 | 2 | 0 |

Warm latency median 96.3 ms, maximum 121.7 ms.

Model loading (including artifact integrity verification and runtime import):
**12.42 seconds**. Process peak RSS: **3061.2 MiB**.
These are local observations, not a throughput SLA or HF CPU Basic sizing claim.

Gold limitations are retained rather than silently repaired: the historical
organization fixture includes its terminal period inside a gold span but expects
the period after replacement. Replacement correctly follows source spans; the
replacement unit test documents that inconsistency separately. Some historical
CITY annotations describe states/rivers, which differs from the prompt's strict
city meaning. Aggregate strict scores are still reported against the unchanged
gold labels. Remaining category/boundary errors and one canary preclude acceptance
of autonomous release. No recognition threshold or name dictionary was tuned to
make the suite green.

### Tests and security qualification matrix

Baseline before implementation: **64 passed, 6 skipped**. Final default suite:
**114 passed, 8 skipped**, one existing Starlette TestClient deprecation warning.
Ruff check and format check pass. Two skips are explicit real-model tests, three
are legacy NLTK/optional characterization cases (NLTK not installed), and three
are existing opt-in OCR/backend tests. The historical tests were not edited.

Separate actual-model pytest run: **1 passed, 1 failed**. Real local recognition
and two-page OCR handoff passed; the zero-canary release qualification failed.
The aggregate evaluator completed successfully and records
`recognition_qualification_passed=false`. Its successful execution does not
change that failed gate. Default CI intentionally does not download a NER model.

| Boundary | Evidence | Result |
| --- | --- | --- |
| Missing/corrupt/pin-mismatched model artifacts | Offline cache and manifest tests | PASS: constant error, no candidate |
| Model initialization/inference exception | Injected failures, no raw exception/document logging | PASS |
| Malformed spans, scores, labels, extra fields, forged copies | Offline strict-schema tests | PASS |
| Untrusted tagger text/metadata | New path accepts validated model spans; historical dictionaries cannot enter release | PASS |
| Overlap/repeat/umlaut/line wrap/spacing | Gold replacement tests and source-offset mapping tests | PASS for replacement; recognition remains imperfect |
| Incomplete/missing/duplicate pages or classification mismatch | Multi-page failures and second-page inference exception | PASS: all-or-nothing |
| Raw Markdown/blocks/alternatives/engine/IDs | Canary-rich mocked OCR evidence and immutable snapshot test | PASS: omitted from candidate/export |
| D1/D2 local handling | Classification-retention and remote-recognizer rejection tests; no remote adapter | PASS within trusted application boundary |
| Empty predictions | Review reason, approval false, automated eligibility false | PASS |
| Unauthorized/unreviewed/expired/forged/mismatched approval | Identity callback, digest/HMAC, expiry tests | PASS |
| Unsupported exports and D1/D2 bare text export | Export policy tests | PASS: blocked |
| Legacy pipeline implicit anonymization | Real chunking entry function rejects anonymize=True | PASS: migration required |
| Model detects every required canary | Actual pinned CPU model, 40 + 10 synthetic cases | **FAIL: one CITY canary** |
| Original OCR runtime/public worker packaging | Existing OCR tests; no worker/API/Docker/runtime dependency edits | PASS regression; no production deployment performed |

Review authorization is a host application integration contract, not a mocked
claim of deployed SSO/ACL enforcement. Tests use synthetic actors/signing keys.
Production approval storage, identity integration, CUDA, live Debian-Devil execution
and a reviewed destination for D1/D2 export remain operator integration work. No
public Debian port was opened, HF deployment changed, production service restarted,
or PR automatically merged.


---

## PR #3 follow-up — recognition and private release hardening

The [follow-up report](anonymization-pr3-hardening.md) supersedes the initial new-engine
results above while retaining them as historical evidence. The actual final model
tests now both pass; zero canaries remain in 40 qualification + 10 validation cases
after the general CITY `location` prompt fix. Strict per-category errors are still
reported; no gold corpus changes were made. Approval exports now require verified
review evidence, destination binding and durable single-use replay protection.
Automated release and deployment remain **NO-GO**. See the linked report for all
comparison metrics, security regressions and four separate readiness decisions.
