# Gate03e — error taxonomy and private review readiness

**Offline synthetic development: GO. Tested fail-closed behavior: GO. Model
qualification: NO-GO. Review readiness: PARTIAL. Private deployment, real sensitive
documents and automated release: NO-GO.** No new independent holdout was created.

## Synchronization and immutable history

Reviewed start `ad9a0003cea9e5dbe39183afbca869c2926b9fff`; existing feature worktree and
draft PR #3 retained. All remote refs fetched. Integration target remains
`281c84172281a2294227f15287a4aded8a7ea948`; ancestry verified, no rebase required.
No reset, force push, other-worktree modification, merge, deployment, service
restart, public listener, remote inference or real sensitive-document processing.

Actual committed recognition/evaluation source:
`094ad6052cde66e5a7204fb614eff10c5572f313`.
Final source-reference classification contract/security verification source:
`25064678071d00e7cd6d99ac49fb5e3f6d6a16e2` (only the private review bridge and tests changed thereafter;
recognition, candidate provenance and evaluation sources are byte-identical).

Gate03c/03d first reports, first provenance, datasets and freeze files remain
byte-for-byte unchanged. `historical.py` pins and verifies their original hashes.
The old frozen 03d runner still refuses updated code instead of presenting a new
run as its first result. Historical failing assertions are explicit controls;
the current candidate coverage gate is a different, also failing, assertion.
All 40+10+120+160 cases are now **development evidence**, not untouched qualification.

## Separate outcomes and measurement scope

`score_outcomes` reports six mutually exclusive document outcomes:

1. Complete candidate with no **measured** source-canary leakage.
2. Returned candidate with a partial mask.
3. Returned candidate with a fully unmasked canary (takes precedence if both occur).
4. Processing blocked by conflicting spans.
5. Processing blocked by model/runtime failure.
6. Other incomplete processing.

Per-canary counts retain both partial/full failures when they coexist. A category
or exact-boundary error can be completely covered and thus have zero measured
canary leakage. A candidate with no measured leakage can still be inexact or unsafe
on other text. Blocked cases return no candidate; their gold spans are not leaked
candidate output. Empty-gold failures still count as incomplete, never success.

Reports separate raw recognizer exact-span P/R/F1, all-document replacement metrics
(incomplete documents count as missing), and returned-candidate-only metrics with
an explicit availability/abstention denominator. Conservative all-source coverage
is named `uncovered_source_canaries_including_incomplete`, not emitted leakage.
The original aggregate evaluator and saved reports remain unchanged.
Unexpected runtime errors are converted to constant safe-blocking codes, with no
exception payload in results. Source-offset and overlap validation are unchanged.

## Failure taxonomy and limited solutions

The [synthetic error taxonomy](anonymization-evaluation/gate03e-error-taxonomy.json)
records exact gold slices, genuine old predictions/scores and causes for all nine
problem documents. It derives from the immutable first03d result, not a new model
claim or changed gold.

| Case/class | Diagnosis and action |
| --- | --- |
| 025, `keine Religion` | Boundary/terminology/taxonomy: model masks Religion only, leaving the negative-status word. Bounded status grammar protects the full phrase. |
| 026, `religiously unaffiliated` | Semantic/terminology miss; Affiliation is falsely ORGANIZATION. Grammar protects the full status expression. The model's header false positive is retained and reported; exact document still fails. |
| 111, `konfesslonsfrei` | OCR i/l substitution defeats literal terminology; model returns no entities. One-confusable long-status matching covers the original slice without rewriting the source. |
| 125, `Hafenbogen Archivs` | Entity boundary/semantic head: model masks Hafenbogen only, omitting seven letters in the genitive head Archivs. No OCR corruption. **Unresolved**; blindly padding following nouns could overmask/cross other entities. |
| 055/063/078/127/137 | Identical bounds conflict: model PERSON (four documents) or ORGANIZATION (one, two occurrences) versus terminology RELIGION. **All five still block**. No span/category is discarded to improve pass rates. |

Religion-topic gold protects terminology even without personal attribution.
A model PERSON score of 0.9998 on the Bahai topic demonstrates why genuine scores
are not calibrated privacy/affiliation evidence. General words/headers also cause
false positives; do not relabel the gold to make them correct. Organization-head
expansion requires context and independent safety evidence, not fixture suffix
padding. Prior company/organization prompt regressions remain historical evidence;
no global threshold reduction or organization dictionary was added here.

Three policies were compared: old literal hybrid control, contextual status grammar,
and grammar plus scoped OCR recognition. Selection criteria were documented before
comparison in [the acceptance specification](anonymization-gate03e-acceptance.md):
no new returned canary, no lower candidate availability/exact match, no new exact-span
false positive in any suite, and at least one canary improvement. Both proposals
pass those development criteria; select grammar plus scoped OCR, improving three
canaries. No post-comparison tuning was performed. These criteria do not qualify
an independently evaluated model or authorize processing/release.

Status grammar protects explicit DE/EN noun/modifier phrases (including declined
German negative determiners and religiously modified English non-affiliation), with
bounded whitespace and Unicode word boundaries. It does not infer a person's belief.
OCR matching accepts exactly one i/l/1 or o/0 substitution in configured lowercase
ASCII status words of 12–40 characters, at whole-word boundaries. Default terms
are status vocabulary, not names; no edit distance, arbitrary fuzzy-name matching,
insertions/deletions, short-word matching or source correction. The source span is
mapped through dehyphenation and labeled deterministic with no fabricated score.
Configuration, bounds, confusable classes and versions enter the candidate digest.
Independent unit negatives cover nonreligious ambiguity, identifier substrings,
combining marks, multiple changes, unsupported substitutions and political status.

## Controlled development results

One actual pinned inference per each of 330 unique texts is shared by the three
fixed policies; replay times are never described as model inference. A preliminary
uncommitted comparison selected the same fixed solution; the final authoritative
comparison repeats it from committed clean source for reproducible provenance.
The final literal-control model span predictions match the first03d raw predictions
for all 160 cases. Model weights/prompts/threshold are unchanged.

| Policy | Suite | No measured leak | Partial-mask docs | Fully-unmasked docs | Safely blocked | Document exact match |
| --- | --- | --- | --- | --- | --- | --- |
| gate03d_literal_control | qualification (40) | 40 | 0 | 0 | 0 | 87.500% |
| gate03d_literal_control | validation (10) | 10 | 0 | 0 | 0 | 80.000% |
| gate03d_literal_control | development03c (120) | 120 | 0 | 0 | 0 | 90.000% |
| gate03d_literal_control | development03d (160) | 151 | 2 | 2 | 5 | 82.500% |
| contextual_status | qualification (40) | 40 | 0 | 0 | 0 | 87.500% |
| contextual_status | validation (10) | 10 | 0 | 0 | 0 | 80.000% |
| contextual_status | development03c (120) | 120 | 0 | 0 | 0 | 90.000% |
| contextual_status | development03d (160) | 153 | 1 | 1 | 5 | 83.125% |
| status_scoped_ocr | qualification (40) | 40 | 0 | 0 | 0 | 87.500% |
| status_scoped_ocr | validation (10) | 10 | 0 | 0 | 0 | 80.000% |
| status_scoped_ocr | development03c (120) | 120 | 0 | 0 | 0 | 90.000% |
| status_scoped_ocr | development03d (160) | 154 | 1 | 0 | 5 | 83.750% |

Model/runtime blocks and other incomplete cases are zero in this actual development
run; their distinct paths are covered by deterministic tests. On the 160-case set:
returned candidates remain **155/160 (96.875%)**, safe abstention **5/160 (3.125%)**.
Returned religion canaries improve **1 partial + 2 fully unmasked → zero**.
One partially masked organization remains; no completely unmasked canary remains
in returned candidates. No-measured-leakage returned candidates improve 151→154;
this does not make those texts universally safe. Exact match improves 132→134/160
(82.5→83.75%). Cases 025 and 111 become exact; 026 retains the ORGANIZATION header
false positive. The same five conflicted documents remain unavailable.

All-document exact source-span comparison on the 160 cases (incomplete counts as
missing). Other suites/categories are fully recorded in the linked machine report:

| Category | Before TP/FP/FN | After TP/FP/FN | Before P/R/F1 | After P/R/F1 |
| --- | --- | --- | --- | --- |
| PERSON | 20/5/2 | 20/5/2 | 0.800000/0.909091/0.851064 | 0.800000/0.909091/0.851064 |
| CITY | 7/2/1 | 7/2/1 | 0.777778/0.875000/0.823529 | 0.777778/0.875000/0.823529 |
| RELIGION | 137/3/13 | 140/2/10 | 0.978571/0.913333/0.944828 | 0.985915/0.933333/0.958904 |
| EMAIL | 3/0/1 | 3/0/1 | 1.000000/0.750000/0.857143 | 1.000000/0.750000/0.857143 |
| PHONE | 2/0/0 | 2/0/0 | 1.000000/1.000000/1.000000 | 1.000000/1.000000/1.000000 |
| ORGANIZATION | 5/12/1 | 5/12/1 | 0.294118/0.833333/0.434783 | 0.294118/0.833333/0.434783 |

Returned-candidate-only religion P/R/F1 is 0.985915/0.972222/0.979021, FP2/FN4;
that subset excludes the five blocked documents and cannot substitute for global
availability or quality metrics. Fully covered wrong-category/boundary predictions
still count as exact-span errors. All other categories' aggregate metrics are
unchanged; no new false positive is introduced on any of the four suites. The
previous literal-hybrid status-control false positives remain development debt.

[Full per-case predictions, errors, outcomes and all metrics](anonymization-evaluation/gate03e-development.json),
[hashes/source/configuration provenance](anonymization-evaluation/gate03e-development.provenance.json).

## Candidate and human-review boundary

New candidate v2 commits the actual model revision, threshold, prompts, terminology,
status/OCR configuration, replacement version/placeholders and normalization into
canonical SHA-256. Only digest, pinned revision and safe policy-version enums enter
ordinary candidate provenance; raw configuration strings and source entities do not.
Configuration drift blocks creation. Complete provenance participates in the existing
release digest. All original sensitivity and false release/anonymity/automation
flags are retained.

The [review/provenance interface and migration specification](anonymization-review-contract.md)
describes the actual typed private bridge. Current authenticated identity, private
operator environment and review ACL are checked before loading candidate text and
again before return. Source-page retrieval verifies authoritative immutable byte
hash, source classification, strict UTF-8 and original character count, then
reauthorizes. An authenticated reviewer can inspect complete original pages and
sanitized candidate, categories, span references, uncertainty, blocking reasons and
configuration provenance within the authorized private application.

This contract has an isolated synthetic test adapter, **no production provider/UI**.
Displaying review material neither completes an immutable review nor creates an
approval. Blocked records have no candidate/digest. Host completion must bind the
exact displayed source evidence/candidate/configuration and current actor, then
use the unchanged reviewed-release authority. Original evidence remains separate,
classified and inaccessible in public metadata/export JSON. Export still requires
its own verified review/ACL/destination/expiry/single-use ledger authorization;
exported JSON never grants transfer permission.

Original v1 candidate serialization and approval signatures remain valid for that
exact object. New v2 provenance cannot be retrofitted into old approvals. Unknown
versions and malformed/missing provenance fail closed. A golden v1 fixture from
the actual reviewed schema and real SQLite consumption verify compatibility; no
key, nonce ledger, stored approval or old candidate was silently migrated/reset.
Legacy review explicitly marks configuration unknown. Full rollback/quarantine
requirements are in the linked contract. No migration/deployment was performed.

## Runtime and tests

Pinned model `VAGOsolutions/SauerkrautLM-GLiNER@ba967ee030b7e679dd5422cb16fcd1b379da38e1`,
CPU, two threads, threshold 0.5; Python 3.11.17, GLiNER 0.2.29,
torch 2.8.0+cpu, transformers 4.57.1, NumPy 2.4.6, Pydantic 2.14.1; Ruff 0.17.1.
Verified local cache and existing locked environments reused. No dependency/service
setup or environment configuration change was necessary.

Final model load **15.254 s**, peak RSS **3018.828 MiB**
versus first03d 3018.203 MiB. On the 160 texts, actual model median/max
**95.934/392.201 ms**
versus historical03d median/max 93.217/125.148 ms; scheduling variance is visible,
so no throughput/SLA improvement or causal neural-runtime regression is claimed.
Same cached predictions, deterministic/replacement replay median is
**0.0883 ms control →
0.1101 ms selected**.
The 40/10/120 suites' actual model median latencies are
92.660/
141.536/
119.453 ms.

The offline audit denies one urllib3 IPv6 capability-check bind attempt; no connect
or DNS event is recorded. No public endpoint/listener or remote model was enabled.
No new container/service was started in this gate. Prior constrained cloud 8 GiB
memory/headroom observations remain historical, not private-host or worst-case
current-workload qualification. Maximum-length throughput, actual private-host load,
identity/UI, encrypted source retention and operational recovery remain blockers.

Exact argv, environment overrides, UTC timing, source SHA and exit statuses:
[recognition/full verification](anonymization-evaluation/gate03e-command-results.json),
[review-classification verification](anonymization-evaluation/gate03e-final-command-results.json),
[final request-reference security verification](anonymization-evaluation/gate03e-reference-guard-command-results.json).

| Check | Result |
| --- | --- |
| Ruff check/format | PASS; 91 Python files formatted |
| Final default OCR suite | **310 passed, 12 skipped**, one existing Starlette deprecation warning |
| Final explicit security subset | **241 passed** |
| Offline/UTF-8/atomic-output subset | **18 passed** |
| Actual pinned smoke/two-page handoff, old50 canaries, selected170 regression | **3 passed** |
| Historical03c control acceptance | **FAILED** (original model-only control) |
| Historical03d first-result acceptance | **FAILED** (immutable first report, not updated candidate inference) |
| Current03e returned-candidate coverage gate | **FAILED** (remaining partial organization canary) |
| Whole opt-in model invocation | **3 passed, 3 failed**, exit 1; no xfail or relabel |
| Fixed-policy development comparison | Selection criteria PASS; **not independent model qualification** |

Default skips are six explicit heavy/model/report gates, two optional NLTK legacy
characterizations, one legacy OCR opt-in, and three live OCR-backend opt-ins.
Unit/security evidence includes actual temporary durable SQLite, concurrency,
termination/restart, key rotation, revocation, destination transport and atomic
files. Identity, review and source storage use isolated synthetic adapters only;
no real production authentication/host test is claimed. The 70 new deterministic
regressions cover outcome separation, scoped status/OCR negatives, provenance,
legacy approvals, unknown versions, source classification, Unicode, authorization
before reads and rechecks, integrity, no approval from review metadata and raw-log
suppression. GitHub CI remains lightweight; exact-head CI is checked after push.
Its green status does not satisfy any of the three deliberately red quality gates.

## Acceptance and independent decisions

The [measurable staged acceptance proposal](anonymization-gate03e-acceptance.md)
separates model-quality targets from live identity/authorization and host readiness.
No new independent corpus is authorized/created before the proposed changes and
thresholds are reviewed. Future thresholds require domain-owner acceptance and
independent representative testing; finite synthetic zero-leakage is no guarantee.

| Decision | Status | Remaining reason |
| --- | --- | --- |
| Offline synthetic development | GO | Bounded local runtime and deterministic/security tests pass; known failures explicit. |
| Fail-closed behavior within tested boundaries | GO | Same five conflicts abstain; runtime/incomplete errors never count as successful candidate output. |
| Model recognition qualification | **NO-GO** | Organization canary survives; 83.75% exact match, precision/recall/availability targets unmet; no independent evaluation of this fix. |
| Human review readiness | **PARTIAL** | Typed authorized original/candidate access is tested; real IdP, source store, completion evidence and private UI integration BLOCKED. |
| Private deployment | **NO-GO** | Actual application/host security, bounded workload and operational acceptance not qualified. |
| Manually reviewed real sensitive-document processing | **NO-GO** | Both model-quality and real host/review integration gates unmet. |
| Automated release | **NO-GO unconditionally** | `anonymous=false`, `automated_release_eligible=false`; no automatic exporter or remote processing. |

PR #3 stays draft/open. No merge, deployment, production activation or automatic export.
