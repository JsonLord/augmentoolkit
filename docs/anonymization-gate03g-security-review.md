# Gate03g — final engineering/security review of draft PR #3

**Recommendation: GO for human-reviewed merge of the library into the development
integration branch after review of these fixes. This is not deployment approval.**
Keep PR #3 draft/open pending that review; do not merge automatically. Real private-host
integration, real sensitive-document processing and automated release remain NO-GO.
`automated_release_eligible=false` is unchanged. No new model, holdout, public API,
service, listener, deployment infrastructure or remote processing was added.

## Scope, Git integrity and evidence

Reviewed starting head: `6171bcfb536f57676b6912ca2c831fd2894d28e2`.
Fetched all remote branches; feature matched that reviewed head and integration
remained `281c84172281a2294227f15287a4aded8a7ea948`. Both are ancestors of the
fix/verification source `9d4d8c103271ef62fee3d06603180aa9ca74267e`. No rebase,
force push or other-worktree modification was performed. Subsequent delivery commits
add review documents/evidence only; final SHA and exact-head CI are in the PR summary.

The [Git integrity and grouped file index](anonymization-evaluation/gate03g-integrity-and-file-groups.json)
records the exact reviewed/runtime PR-diff SHA-256, every changed file's Git blob and
content hash, ancestry and all 43 preexisting evaluation/corpus hashes. All preexisting
evaluation evidence and frozen corpora are byte-identical to the reviewed head.
`verify_historical()` also verifies its eight hard-pinned historical artifacts and
first-result/corpus relationships. No gold data or failed quality gate was changed.

This is a fresh source-level review and adversarial regression pass over the library
trust chain and its integration contract. It is not qualification of a real host or
identity provider. The complete PR diff at runtime source contains 111 files, much of
it retained evaluation evidence; the index groups it for review rather than deleting
or rebasing away published work.

## Findings and disposition

Severity describes potential privacy/authorization impact in a future integration.
No production exploit or sensitive-data incident is claimed. All six confirmed gaps
below are fixed in `9d4d8c1`, with focused regressions in
`ocr/tests/test_gate03g_security_audit.py`.

| ID / severity | Confirmed behavior before fix | Disposition |
| --- | --- | --- |
| G01 / High | A completed review naming the right v3 candidate but the wrong original OCR evidence digest could receive approval. | v3 approval/export now require `review.evidence_digest == candidate.correction.original_evidence_digest`, in addition to candidate/classification/destination and host checks. |
| G02 / Medium | SQLite consumption checked expiration before `BEGIN IMMEDIATE`; a lock wait could consume an already expired approval. | Recheck expiry inside the acquired write transaction. Real SQLite contention test confirms denial and no inserted nonce. |
| G03 / High | Review/ACL/policy revocation during persistent consumption could still return the serialized export. | Reauthorize completed review, candidate policy and approval time after consumption, before returning bytes. Failure burns the nonce and returns no export. |
| G04 / High | Transfer permission revoked while a private artifact was being written did not prevent its subsequent publication. | Recheck current transfer authorization and private directory ownership/mode immediately before the atomic hard link. Denial removes staging and publishes nothing. |
| G05 / Medium | Transfer review lookup could return an attestation whose review ID differed from the requested export record's review ID. | Require exact review-ID equality as well as original candidate/evidence/class/destination bindings. |
| G06 / Medium | v1/v2 eligibility was implicit; a missing correction policy denied v3 but did not require an operator decision for uncorrected schemas. | Add explicit `legacy_policy` / host `legacy_release_allowed` for v1/v2. Missing, exception or non-literal-True denies approval/export. No implicit fallback from v3. |

The first five reproduction tests failed against the reviewed runtime. Two further
revocation-during-consumption cases failed after the initial fixes and before G03.
They are diagnostic reproductions, not passing qualification runs; their summaries
and log hashes are retained in the [findings record](anonymization-evaluation/gate03g-security-findings.json).
The final 21 audit regressions pass. Existing regression coverage is retained.

Two operational limits are explicitly **not fixed by pretending to have a transaction**:

- `CorrectionHost` is a protocol requiring durable reauthorization/CAS/head publication,
  immutable records and single-use state in one real host transaction. The isolated
  fixture has `production_ready=false`, `durable_corrections=false` and an in-process
  RLock; it proves neither cross-process durability nor crash/restart atomicity.
- Filesystem publication and external identity/ACL stores are not one atomic resource.
  Rechecks close the demonstrated slow-I/O windows, but do not serialize arbitrary
  concurrent revocation with the last link/return instruction. A real host must define
  the authorization linearization point and serialize its revocation/publication policy.
  A directory-sync error after link can leave a **complete 0600 private artifact** while
  returning failure. The new regression verifies those bytes/classification; no partial
  file or automatic retry/transmission is allowed. Reconcile the exact artifact digest
  under current authorization rather than assuming rollback. Lost correction replies
  similarly require inspection of the stored publication pointer.

## Security architecture and runtime responsibilities

The trust chain remains:

`classified OCR → immutable machine observations → authenticated original-span proposal
→ immutable correction provenance/new candidate → separately completed human review
→ signed single-use export → separately authorized approved private delivery`.

| Runtime module | Responsibility and trust boundary |
| --- | --- |
| `engine.py`, `handoff.py` | Validate complete OCR/classification, use trusted local recognition, allowlist candidate fields, preserve source coordinates and uncertainty. A caller-supplied OCRResult is not proof of authentic OCR: real host registration must attest its origin and retain complete immutable evidence. |
| `model.py`, `recognizer.py` | Verify pinned artifact hashes; load local-only GLiNER; bounded token/windows, actual source offsets and serialized inference. No remote inference or automatic model installation. |
| `detectors.py`, `status.py`, `terminology.py`, `normalization.py`, `replacement.py` | Deterministic protections/replacement, mapped OCR normalization and fail-closed conflicting spans. No recognition changes in this gate. |
| `schemas.py`, `candidate_io.py`, `provenance.py` | Strict typed version dispatch, configuration commitments and candidate metadata allowlist. Digests commit content; they do not authenticate an untrusted producer. |
| `private_review.py`, `host_adapter.py` | Opaque authenticated sessions; current ACL/revocation/private environment; source hash/UTF-8 verification; authoritative completed review and destination lookup. No production IdP or review UI is supplied. |
| `corrections.py` | Original evidence/span/config/prior-head binding, explicit superseded observations, reject conflicting edits, retain machine history, new v3 candidate and receipt. Host must implement durable publication. |
| `release_policy.py`, `approval_ledger.py` | Exact candidate/source/review/class/destination/format/time binding, schema eligibility and SoD callbacks, HMAC approval and real persistent single-use SQLite consumption. |
| `private_transport.py` | Only registered private local directories, exact authorized export artifact, separate current transfer ACL, no URLs/redirects/fallback or classification downgrade. |
| `atomic_output.py`, `recovery.py`, `__main__.py` | Private complete/no-overwrite candidate files, sanitized errors, explicit classified staging cleanup and no release shortcut. Candidate creation is not durable approval/publication. |

Machine observations and human effective state stay distinct private records. Original
pages are reviewed in full, not only model masks. Correction IDs/provenance hashes do
not serialize raw source identifiers; private candidate text can still contain misses
and must never be treated as anonymous or logged. Validation errors at service/release/
transport boundaries remain sanitized. No new logging of classified payloads is added.
A missing recognition result or runtime failure never triggers a remote fallback.

## Candidate versions, operator policy and migration

Candidate v1/v2/v3 serialization and digests, approval v2, reviewed-export v2, signing
keys and ledger formats are unchanged. The original v1 golden digest remains
`eb3ab0d101360654f07aa4c614f12873646d44e1d21b1e6cf096033225dec435`.
Strict literal versions, forbidden extras and instance revalidation reject parent-type
coercion and malformed version copies. Suppression-only v3 candidates are also tested;
absence of a human replacement tag does not remove the mandatory correction policy.

**Intentional eligibility change:** v1/v2 now require an explicit operator policy.
Direct callers supply `legacy_policy(candidate, verified_review)`; bridge users must
implement `legacy_release_allowed` in the real authoritative host. Do not simply
return True, trust a requested schema version or copy the test fixture. Verify exact
registered producer snapshot, original evidence/review binding, eligible schema/model/
configuration, current policy and absence of disguised correction history. Legacy v1
has unknown configuration; either deny it or document a specific manually reviewed
operator exception. New uncorrected v2 is included in this policy despite the `legacy`
parameter name. A real provider is unavailable; this production decision remains blocked.

V3 still needs separate `correction_policy` / `corrected_release_allowed`, complete
original-evidence binding and the real host's current separation-of-duties policy across
all correction authors. Missing policy never falls back to v1/v2 permission. Both
policies require literal True and are checked during approval, export and final export
authorization after consumption. The fixture policies are explicit synthetic opt-ins.

Old candidates/approvals remain usable for their original exact digest **only when**
the operator explicitly enables that version and every existing review/TTL/ACL/
destination/nonce condition still holds. An unconfigured caller now fails closed;
this is not a drop-in behavioral compatibility claim. No stored approval is rewritten,
consumed nonce erased or prior candidate mutated. A new correction always needs a
new completed review/approval. Approved D1/D2 JSON still grants no permission for an
arbitrary external destination and retains `transmission_authorized=false`.

## Verification scope and model limitations

[Exact commands, environment overrides, source SHA, timestamps, exit codes and summaries](anonymization-evaluation/gate03g-command-results.json)
record final local qualification. Test selections overlap; do not add their counts.
The [fresh nine-case model demo](anonymization-evaluation/gate03g-corrections-live.json)
and [two exact controls](anonymization-evaluation/gate03g-correct-controls-live.json)
use the unchanged pinned model on existing synthetic development examples only.
No new holdout or model-selection experiment is created.

The six manual corrections recover the existing partial organization case and five
conflict-blocked cases, retain D2 and deny release without approval. Forged span and
unauthorized submissions are rejected in each. Three existing exact controls remain
unchanged; controls 001/003 retain their documented overmasking. These manually chosen
synthetic gold spans are not model true positives or proof of universal privacy safety.

Real components exercised here: pinned CPU inference with verified cached weights,
actual SQLite lock contention/consumption/restart/process tests, private filesystem
writes/link/sync failures and UTF-8 fallback. Mocked/synthetic components: identities,
completed reviews, ACLs, destination approval and correction persistence. No actual
provider, real document, host deployment, HF runtime or remote OCR endpoint is tested.

Historical Gate03c/03d first-result failures and the uncorrected Gate03e organization
canary remain unchanged. Recorded Gate03d/Gate03e opt-in assertions are rerun separately
and remain failing; the older Gate03c first result is hash-verified without rerunning
its costly model comparison. Default green CI excludes opt-in quality/model tests.
No failed release-qualification assertion is labeled a successful benchmark.

Model/runtime remains `VAGOsolutions/SauerkrautLM-GLiNER` at
`ba967ee030b7e679dd5422cb16fcd1b379da38e1`, threshold .5, CITY prompt `location`,
CPU/two threads and Python 3.11 with existing hashed locks. Initialization/inference
latency, peak RSS and dependency versions are recorded in each new demo report.
Offline flags and Python socket-audit denial are used; these are not proof of arbitrary
native egress confinement on a production host. No new listener is opened, no service
restarted and no dependencies, HF image or original deployment packaging changed.

## File groups, deployment blockers and rollback

Use the [complete grouped file index](anonymization-evaluation/gate03g-integrity-and-file-groups.json)
to review security core/local integration, model recognition/locks, correction workflow,
tests/CI, frozen corpora/evaluation evidence, synthetic evaluation tools, architecture
docs and disabled deployment specifications separately. Evaluation tools import test
fixtures only for explicit synthetic runs; they are not production worker entrypoints.

Keep all historical evidence here. A future additive PR may organize future model
experiments and artifact indexing, but must preserve old evidence by content hash and
stable links. Do not delete raw predictions, trim failed reports, remove security tests
or rewrite published commits merely to shrink this PR.

The disabled staging manifest and service template remain specifications, not installed
infrastructure. Before real private integration: real authenticated host/review/UI and
immutable source/export stores; durable correction transactions and crash qualification;
approved private physical sinks; encrypted 0700/0600 storage/retention/cleanup/backup;
pinned ledger identity, real signing-key rotation and trusted clock; actual host startup/
restart/load/headroom/offline network containment. Profile remains one model, two
threads, one job, bounded queue, 8 GiB worker cap and 12 GiB/4-vCPU host recommendation.
The actual host must demonstrate >=25% headroom under representative maximum load.
Independent local ledgers must never share a signing key across hosts; qualify a genuinely
shared durable ledger or isolate actual key domains. No production adapter is supplied.

Rollback requires separate deployment authorization. Preserve all source/machine/
proposal/candidate/review/export and nonce history; stop intake, quarantine unsupported
candidate versions and disable exports until explicit version policies are validated.
Do not downgrade v3 to v1/v2, reset a consumed ledger, restore an old ledger with a live
key, or delete a new candidate because its publication reply was lost. Removing this
new policy enforcement on rollback reopens the reviewed gaps: do not treat old code
as production-safe. Retention/legal hold and secure deletion remain operator decisions.

| Decision | Result |
| --- | --- |
| Merge library into development integration branch | **GO recommendation after human review** of the fixes, policy migration and tests. Keep experimental/private status; no automatic merge. |
| Real private-host integration | **NO-GO**: no real identity/review/UI, durable correction adapter or target-host operational qualification. |
| Sensitive-document processing | **NO-GO**: host blockers plus unresolved model coverage/review risk. |
| Automated release | **NO-GO**: invariant false, recognition unqualified and no approved automatic release policy. |

## Final local verification at the fixed runtime source

| Command scope | Result |
| --- | --- |
| Ruff check / format | PASS; 96 files formatted |
| Complete lightweight suite | **387 passed, 13 skipped**, one existing Starlette TestClient deprecation warning |
| Explicit correction/security selection | **230 passed**, included in complete suite |
| Legacy/version compatibility selection | **19 passed, 39 deselected**, included in complete suite |
| Offline/runtime/UTF-8 selection | **18 passed**, included in complete suite |
| New adversarial audit tests | **21 passed**, included in complete/security suites |
| Actual pinned correction demonstration | Nine existing cases, six corrected candidates; seeded coverage and release denial hold |
| Actual pinned exact controls | Two additional existing cases, unchanged exact candidates; release denied |
| Opt-in recorded quality/correction evidence | **1 passed, 2 failed, 4 skipped**; quality failures remain Gate03d first result and Gate03e current uncorrected canary |

Default skips: seven opt-in anonymization model/evidence tests, three optional legacy
NLTK characterization cases, and three unrelated live OCR/backend cases. In the
opt-in evidence run, the four older inference-based tests are deliberately not rerun;
only the two saved failing quality reports and the freshly rerun correction demo
acceptance are checked. Gate03c first-result evidence is hash-verified and remains
failed, not retested or relabeled. The opt-in command has exit code 1, not a successful
release qualification.

Fresh live model source is the exact fixed runtime commit `9d4d8c1`. Nine-case model
initialization: **21.300 s**; capture median/max **111.92/123.46 ms**; peak RSS
**3025.52 MiB**. Supplemental two-case initialization: **22.810 s**; capture median/max
**542.93/549.70 ms**; peak RSS **3025.61 MiB**. Capture includes model/rules and
source/candidate commitments, not only inference. No throughput/headroom SLA is claimed;
cold-run/environment variation is retained rather than averaged away. Each initialization
records one denied package socket-bind attempt, with no recorded DNS/connect event.
All 11 new inference calls use local synthetic text; there is no external backend call.
See [live provenance](anonymization-evaluation/gate03g-live-provenance.json).
