# PR #3 recognition and private release hardening

This follow-up supersedes the initial new-engine recognition results while
preserving the historical legacy report and reviewed-head evidence.

## Git and scope

- Reviewed/old feature HEAD: `57ba4471797099a5e92255961ca1e38f1fa14d03`.
- Integration branch: `jules-agent-0-deployment-9610133099829518547`, fetched HEAD
  `281c84172281a2294227f15287a4aded8a7ea948`. It had not advanced: no rebase,
  conflict, reset or force-push was needed.
- Same branch/worktree: `feat/ocr-anonymization-failclosed`,
  `/workspace/augmentoolkit-anonymization`. Other worktrees were not modified.
- Delivery uses the same draft [PR #3](https://github.com/JsonLord/augmentoolkit/pull/3).
  The final commit and exact-head CI URL are recorded in the delivery message/PR.
- No HF deployment, production restart, listener, model-weight change, external
  processing, automatic export or automatic release was enabled.

## Gate A — exact diagnosis and controlled comparison

Pinned model: `VAGOsolutions/SauerkrautLM-GLiNER`, revision
`ba967ee030b7e679dd5422cb16fcd1b379da38e1`. Native CPU runtime and dependency
locks are unchanged. Only the general CITY prompt changes from `city` to `location`.
Threshold remains 0.5; the ORGANIZATION prompt remains `organization`.

The reviewed-head failure is synthetic case `baseline-14`:

```text
Original:   Welcome to Los Angeles, California.
Normalized: Welcome to Los Angeles, California.
Gold:       CITY [11:22] = Los Angeles; CITY [24:34] = California
Baseline:   CITY [11:22], score 0.9600135684013367; no accepted [24:34] span
Tokens:     <bos> ▁Welcome ▁to ▁Los ▁Angeles , ▁California . <eos>
```

At diagnostic threshold 0.1, the second span is predicted as `organization` with
score 0.18358616530895233, rather than city. A state is outside the narrow `city`
prompt, despite the corpus deliberately mapping it to the CITY privacy placeholder.
Normalization is identity; tokenization contains the complete word as one token;
there is no OCR spelling variant or broken-word mechanism in this case. Lowering
the operating threshold to 0.35 changes neither suite's results and does not solve
this semantic category mismatch.

The accepted `location` prompt covers cities and geographic places (including
states/rivers), matching the original privacy-category corpus. This is an explicit
semantic broadening of the CITY placeholder, documented in the architecture and
configuration. It may mask non-city places; it does not claim administrative city
classification. No names, gazetteer entries or fixture-specific spellings were
added to runtime detectors, and no gold corpus/legacy tests were edited.

[Comparison JSON](anonymization-evaluation/pr3-recognition-comparison.json) includes
all exact per-category span metrics, leakage, exact match, language/noise groups,
latencies and synthetic diagnostic payloads. Its source text is intentionally
synthetic for this requested diagnosis; normal runtime/aggregate logs never include
private text. The investigation command accepts only repository fixture suites.

| Variant | Qualification exact match | Validation exact match | Remaining canaries Q / V | ORG FP/FN Q | ORG FP/FN V | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| reviewed_baseline | 82.5% | 70.0% | 1 / 0 | 3/1 | 2/0 | Historical control |
| lower_threshold | 82.5% | 70.0% | 1 / 0 | 3/1 | 2/0 | Reject: no improvement |
| geographic_prompt | 87.5% | 80.0% | 0 / 0 | 2/1 | 0/0 | Accept: general semantic fix |
| geographic_and_company_prompt | 82.5% | 70.0% | 1 / 1 | 0/2 | 0/1 | Reject: new organization leaks |

Organization error investigation on the reviewed baseline:

- `baseline-26`: a river/place is tagged ORGANIZATION with score 0.9477. The
  geographic prompt fixes the category competition without a literal-name rule.
- `baseline-31`: an OCR-damaged place name remains tagged ORGANIZATION. Its
  characters are masked, but the label is wrong; the strict span/label metrics
  retain this error. No unsupported spelling normalization was invented.
- `baseline-37`: prediction `[11:21]` excludes the terminal period; the gold
  ORGANIZATION span is `[11:22]`. This counts as one FP and one FN. All sensitive
  letters are covered. The historical gold also expects that period outside the
  placeholder, an existing boundary/expected-string inconsistency retained intact.
- `validation-04`: a real city is tagged ORGANIZATION (score 0.9306) by the narrow
  prompt configuration; the location prompt corrects it.
- `validation-05`: the narrow configuration tags a river mention ORGANIZATION
  (score 0.8391). The location prompt instead masks it as CITY, which remains one
  broader-place false positive against validation gold. This tradeoff is reported.
- `company or organization` avoids some false positives but misses true
  organizations in BOTH suites, introducing one canary leak per suite. It is
  rejected. This failed experiment is not presented as a successful qualification.

The 10 validation cases were separate at task creation. They were used to compare
these limited variants, so they are now development validation, not untouched
held-out evidence. A new independent corpus is required before generalization or
production recognition claims. Threshold search, proper-name dictionaries, gold
repair and broad OCR spelling substitution were not used.

### Final actual-model metrics

[Final aggregate JSON](anonymization-evaluation/cpu-hardening.json) is a new artifact;
[cpu-pinned.json](anonymization-evaluation/cpu-pinned.json) preserves the original
reviewed-head result. Scores use exact original `(start, end, label)` matches.
Canaries check uncovered alphanumeric source characters, including partial masks.
Wrong labels can be privacy-masked while still counting as FP/FN.

#### Qualification — 40 documents

| Category | Precision | Recall | F1 | FP | FN | Canaries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PERSON | 0.857 | 0.900 | 0.878 | 3 | 2 | 0 |
| CITY | 0.941 | 0.941 | 0.941 | 1 | 1 | 0 |
| RELIGION | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| EMAIL | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| PHONE | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| ORGANIZATION | 0.333 | 0.500 | 0.400 | 2 | 1 | 0 |

Exact match: **87.5%**; blocked documents: 0. Warm median: **92.06 ms**; maximum: **148.00 ms**.

#### Validation — 10 documents

| Category | Precision | Recall | F1 | FP | FN | Canaries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PERSON | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| CITY | 0.833 | 1.000 | 0.909 | 1 | 0 | 0 |
| RELIGION | 0.667 | 1.000 | 0.800 | 1 | 0 | 0 |
| EMAIL | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| PHONE | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| ORGANIZATION | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |

Exact match: **80.0%**; blocked documents: 0. Warm median: **96.53 ms**; maximum: **123.49 ms**.

Model load including integrity verification and imports: **32.33 s**.
Process peak RSS: **3017.11 MiB**. Two inference threads; cloud
container quota four CPUs / 32 GiB. No GPU or live private document was tested.
NER-only and combined detector metrics are separately recorded; their counts
match on these two corpora. These small synthetic observations are not an SLA.

## Gate B — approval and export enforcement

The original v1 policy bound only a digest and expiration. A signed approval could
be replayed and did not carry reviewer, evidence, sensitivity, destination or format.
The old caller-supplied `review_completed=True` flag was not evidence of review.
Those contracts are intentionally replaced, without a compatibility bypass.

The host MUST provide:

1. `verify_review(actor, review_id)`: authenticate the current actor/session and
   retrieve an independently stored, completed, immutable review record. It must
   verify reviewer identity and completed review evidence, returning the exact
   candidate digest, sensitivity, destination, completion timestamp, opaque subject,
   review record ID and evidence digest. Returning request echoes or a completed
   boolean is prohibited. The library supplies no identity provider or allow-all
   implementation.
2. `authorize(verified_review, scope, destination)`: check the verified subject's
   current `anonymization:release` permission and destination/classification ACL.
   This check runs again at export, along with independent review verification.
3. An operator-controlled destination registry: IDs, private/public class and
   sensitivity allowlist. Unknown/mismatched IDs and D1/D2 public classes are
   rejected regardless of a permissive host callback. Destination mapping must
   remain immutable for issued approvals; changed entries invalidate approvals.
4. Atomic durable single-use consumption shared by all instances using the key.
   The provided local SQLite ledger is mode 0600 and tested across instances and
   races. Deleting/replacing replay state requires signing-key rotation. A crash
   after consumption burns the approval; delivery retries need new authorization.

V2 HMAC approvals sign the full review attestation, exact candidate digest,
classification via that attestation, full destination policy, format, random nonce,
issuance and expiration. Maximum lifetime is 900 seconds from review completion,
with shorter TTL allowed. Stale/future evidence, changed reviewer/evidence/digest,
expired/forged signatures, format/destination substitutions, revoked ACLs, replay
and unavailable replay storage fail closed. Old boolean review calls and v1 approval
objects cannot enter the new export path.

Export remains a LOCAL operation; it does not send anything. D1/D2 require JSON
and retain classification, destination and `transmission_authorized=false`. An
approved JSON file does not permit transmission to arbitrary external providers.
A separate private application transport must resolve the registered ID to the
approved physical sink, enforce its current D1/D2 authorization and prevent
caller URL substitution/redirects. Physical destination attestation and real
identity/review integrations have NOT been deployed or tested here. Candidates
always retain `anonymous=false` and `automated_release_eligible=false`.

## Gate C — runtime and atomic output

- Removed chunking's import-time `nltk.download()` AND eager HF tokenizer loading.
  Sentence tokenization uses existing cached data, then corpus-free Punkt or a
  simple fallback. Tokenizers are loaded lazily with `local_files_only=True`;
  missing caches use a reversible UTF-8 byte estimate with two reserved tokens.
  This keeps ordinary chunking operational with potentially smaller chunks. Exact
  model-specific token accounting requires operator-provisioned cache artifacts.
- Regression tests import the actual chunking module while download/load hooks
  would fail, and execute ordinary chunking. A separate actual offline-environment
  smoke test also passes without NLTK or the default tokenizer cache; see
  [offline-chunking-smoke.json](anonymization-evaluation/offline-chunking-smoke.json).
- The candidate CLI originally opened the final path before writing; a write/disk
  error could leave a partial file. It now stages mode-0600 bytes in the same
  directory, checks the full write, flushes/fsyncs, and atomically hard-links to a
  nonexistent target. Existing files, symlinks and racing creators are never
  replaced. Write, flush, fsync, short-write and link failures publish no target.
- Staging cleanup runs on ordinary errors. A kill/cleanup failure may leave a
  hidden 0600 classified staging file; it is not an approved export. Publication
  requires hard-link support. Directory-entry durability after power loss is not
  claimed. No raw inference payloads, paths, tokens or private documents are logged.

## Gate D — executed evidence and readiness

- Complete lightweight suite: **165 passed, 8 skipped**, zero failures; one existing
  Starlette TestClient deprecation warning. Skips: two opt-in model tests, two
  unavailable-NLTK legacy characterization tests, one legacy opt-in integration,
  and three existing live OCR/backend cases. Historical gold/tests remain unchanged.
- Actual pinned-model pytest: **2 passed, 0 skipped, 0 failed**. The zero-canary
  release-qualification assertion ran and PASSED. This differs from the reviewed
  head's actual `1 passed, 1 failed`, preserved in the historical report.
- Actual local CPU aggregate evaluation: completed with
  `recognition_qualification_passed=true`, but `automated_release_eligible=false`.
- Ruff check and formatting cover `ocr`; the new standalone offline chunking
  helper also passes lint/format checks. Security regression coverage includes
  authenticated review/destination/format binding, stale and altered evidence,
  expiry, ACL revocation, durable concurrent replay, ledger errors/permissions,
  legacy approval rejection, atomic-write failures and download-free imports.
- Live recognition uses the actual pinned CPU model. OCR evidence, reviewer
  identity, completed-review records and destination ACLs are synthetic/mocked;
  SQLite storage, filesystem atomic operations and offline chunking are real local
  operations. No live Debian identity/destination integration or external OCR call
  is claimed.

| Decision | Status | Scope / remaining gate |
| --- | --- | --- |
| Code/security boundary for reviewed private testing | **GO** | Local tested library/CLI with manual review enforced; production callbacks and registry must be supplied and audited. |
| Model recognition qualification | **PASS for the specified 50-case synthetic canary gate; PARTIAL general accuracy** | Zero remaining canaries, but FP/FN/boundary errors remain; organization strict F1 is 0.400 on the small qualification set. New independent validation is required. |
| Deployment readiness | **NO-GO** | No deployment authorized/performed; real private-host identity, review store, durable state and physical destination approvals remain unqualified. |
| Automated release readiness | **NO-GO** | Always disabled; finite synthetic success is insufficient to remove manual authorization/review or enable external processing. |
