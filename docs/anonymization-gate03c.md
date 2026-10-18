# Gate 03c — private anonymization integration qualification

**Offline private synthetic testing: GO. Private deployment: NO-GO. Real sensitive-document
processing: NO-GO. Automated release: NO-GO.** The integration code/security tests pass,
but the first untouched holdout has two fully unmasked RELIGION canaries and production
identity/review/destination integration is BLOCKED. Nothing was deployed or enabled.

## Synchronization and exact source references

- Reviewed HEAD: `176176a6e13dfb1deb1a6f197b7fa46515b0e1f6`.
- Fetched all remote branches. Integration base remains
  `281c84172281a2294227f15287a4aded8a7ea948`; it did not advance, so no rebase,
  reset, stash, force-push or other-worktree modification was needed.
- Worktree/branch: `/workspace/augmentoolkit-anonymization`,
  `feat/ocr-anonymization-failclosed`; same draft PR #3, no merge.
- Freeze commit, before any holdout inference: `9236acf88a93630782308d1b99206f6e2c3c65a9`.
- Untouched first-result commit: `3323715c30f08da8f867e97925462dd965f2693d`.
- Initial integration qualification code: `5653ab71ab78aa8b2d5abfd41b6cd59076dec097`.
- Final security code and recorded verification source: `793670ed41cda07c538aa660c73a43d401ff598b`.
  Final delivery/documentation SHA and exact-head GitHub CI are in the delivery message.
  Base ancestry was checked with `git merge-base --is-ancestor`.

## Gate 01 — private-host readiness and staging specification

The [staging manifest](../ocr/deploy/private-anonymization/staging.json),
[disabled service template](../ocr/deploy/private-anonymization/anonymization-staging.service.disabled)
and [provisioning/rollback runbook](../ocr/deploy/private-anonymization/README.md)
are prepared for review. Deployment is explicitly disabled, entrypoints are unset,
production identity is BLOCKED and no production host was installed or restarted.
The manifest is a contract; an actual persistent host application is not supplied
or claimed to enforce its queue/input limits yet.

Proposed baseline: **one model instance, one active job, two inference threads,
four vCPU, 8-GiB worker memory ceiling**, and at least 12 GiB host RAM for additional
system/application headroom. The host RAM figure is a staging proposal, not a
universal minimum. Actual host load and maximal documents remain unqualified.
The persistent worker must initialize/verify the local model once at startup and
fail readiness on any integrity or storage/adapter failure; never download on jobs.

Three bounded local container probes used pinned Python 3.11.17 and the existing
local image only as an interpreter. Pinned private NER packages, source and model
cache were mounted read-only. Containers used `--network none`, no port bindings,
read-only root, no capabilities, no-new-privileges, UID/GID 1000, private writable
state and tmpfs. Each ran alongside **1536 MiB physically touched memory plus CPU
load**, inside the same 4-vCPU/8-GiB cgroup. These are test containers, not service
installations or deployment artifacts.

| Probe | Startup ms | Warm median ms | Model peak RSS MiB | Total cgroup peak MiB | Headroom MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| first | 23588.27 | 146.95 | 3020.44 | 4508.41 | 3683.59 |
| restart | 18746.58 | 110.22 | 3020.96 | 4495.07 | 3696.93 |
| native-verification | 17214.57 | 94.68 | 3020.81 | 4495.09 | 3696.91 |

All starts reconstructed the model from intact offline cache. Source/revision and
artifact hashes are pinned; corrupted model-cache tests remain fail-closed.
The final probe measures **zero native TCP listeners** while the model is resident.
One Python `socket.bind` attempt from `_has_ipv6` capability probing was denied by
the audit hook; model initialization still succeeded. We do **not** claim zero
attempts. Socket connect/DNS/bind hooks and Docker network isolation prevent
unapproved egress/public listeners. No host ports were published. This does not
qualify a separate Debian host's firewall or network policy.

Exact commands, interpreter/image identity and source hashes:
[runtime provenance](anonymization-evaluation/gate03c-runtime-provenance.json).
Runtime helpers were uncommitted at execution; that fact and the then-current HEAD
are recorded rather than claiming a clean run. The final native-probe source hash
is byte-identical to commit 5653ab7. Initial/restart artifacts retain their earlier
per-result source hashes. Model dependencies use the existing hash locks.

Permissions: private state/sink directories 0700; ledger, candidates and deliveries
0600, current UID ownership checked. Model cache was mounted read-only. Production
encrypted-volume, ACL, secret-store and power-loss durability checks remain BLOCKED.
Abandoned staging cleanup uses exclusive directory locking; writers use shared
locks. Real killed-writer tests verify only prefixed old private stages are removed,
with original evidence and symlinks retained. Unlinking is not secure erasure.

## Gate 02 — real identity/review adapter contract

`ocr/anonymization/host_adapter.py` defines `HostSecurityAdapter` and
`HostAuthorizationBridge`. No identity provider, allow-all callback, user-supplied
subject or boolean approval path is implemented as a production default.

| Host method | Mandatory authoritative behavior |
| --- | --- |
| `authenticate(opaque_credential)` | Validate real server session/provider evidence, issuer/audience/signature where relevant, current revocation and expiry; return verified subject/session, never requested identity. |
| `load_review(review_id)` | Retrieve immutable completed-review evidence and digest/candidate/destination binding, plus current revocation/expiry state. Requests must not author the attestation. |
| `candidate_exists(digest)` | Verify the exact candidate against an immutable authoritative candidate store. |
| `load_export(artifact_digest)` | Retrieve the exact successfully authorized export reference, original candidate/review/evidence digests, destination, classification, expiry and revocation state. No request-side registration. |
| `current_acl(identity, scope, sensitivity, destination)` | Check current release or separately scoped private-transfer authorization on every operation. |
| `destination(id)` | Resolve only operator-approved policy, never request URLs/paths, public fallbacks or stale policy snapshots. |

The bridge validates authenticated identity expiry, completed-review state,
revocation, candidate digest, current destination policy and ACL at issuance/export.
Delivery additionally reloads the export AND its original review, checks both for
revocation/expiration and evidence/digest consistency, then checks the separate
current transfer ACL. This closes a discovered post-export revocation boundary:
export success is not perpetual permission to deliver.

`ocr/tests/fixtures/isolated_host.py` is the isolated synthetic test adapter only;
`production_ready=False`. It uses opaque in-process credentials and immutable test
records. It is not loaded by production configuration. The cloud environment has
no configured identity/review/security provider. Existing HF account authentication
is not an immutable review/ACL adapter. **Production identity integration: BLOCKED.**

## Gate 03 — durable authorization state

SQLite now has explicit one-time provisioning (`initialize=True`) and pinned
`ledger_id` on restart. Ordinary startup/connect uses existing mode-rw storage;
missing state is never silently created. File/inode/identity, mode/owner, private
parent permissions and SQLite integrity are validated. Replacement, corruption,
missing storage and insecure permissions fail closed. No destructive migration of
an existing authorization database is automatic.

Executed against real local filesystem SQLite, not an in-memory mock:

- Eight simultaneous calls from four processes consume a nonce exactly once.
- A new process/ledger instance retains committed consumption and denies replay.
- Killing a process during an uncommitted transaction rolls back the insert; an
  earlier committed nonce remains consumed after restart/recovery.
- Expired approvals and unavailable/corrupt/missing/replaced state cannot release.
- New signing keys reject old signatures, can issue valid new approvals, and retain
  the existing ledger; rotation does not reset replay history.
- Consumed IDs are retained rather than expiry-pruned, avoiding resurrection after
  clock rollback. Capacity monitoring/archive requires an explicit key/domain plan.

`validate_key_topology(..., signing_keys=...)` computes fingerprints from actual
keys, so different aliases for the same secret cannot hide a shared domain. Shared
keys across hosts are rejected, including a boolean claim of shared durability.
Only single-host SQLite or distinct actual per-host signing keys are qualified in
this implementation. A genuinely shared durable backend is **not implemented or
qualified**. Physical reboot, disk loss and the production volume remain untested.

## Gate 04 — explicit private transport evidence

`PrivateFileTransport` implements only approved **local private filesystem** delivery,
with immutable destination ID/root registry, canonical path and no-symlink opening,
pinned root device/inode, typed allowlisted export payload, retained classification,
mode-0600 atomic no-overwrite publication and file/directory fsync. It imports no
HTTP client and has no URL, redirect, public endpoint, remote fallback or caller
filename/path interface. Tests reject URL inputs, redirect metadata, unknown/public
IDs, replacement/symlink roots, modified/unregistered bytes and extra raw metadata.

Export alone does not invoke delivery or grant transport permission. Delivery
requires fresh credentials, an authoritative exact exported-artifact record, live
review validity and `anonymization:private-transfer`. Both review/export revocation
and expiry after export block delivery. The artifact remains D1/D2 and retains
`transmission_authorized=false`; a receipt describes only the specifically authorized
local file operation. A crash or directory-fsync error after atomic publication may leave a complete
classified destination file without a receipt. Reconcile its hash manually; retries
never overwrite it and no automatic retry/transmission is scheduled.
No remote transport was activated; every test used synthetic
fixtures in private temporary directories. Physical production destinations remain
operator approval work, not inferred from a label.

## Gate 05 — untouched holdout first result (FAILED)

Frozen SHA-256: `0174fb41d34d0a4db18877c68fb70149eaa67c05da86608d5da4ff8765a52c15`.
The 120 new synthetic cases contain 60 German and 60 English cases, twelve novel
entity sets across ten contexts, 24 OCR-noise cases, organization names, ambiguous
geography, explicit affiliations/religious references, repeated identifiers and
negative controls. They were not used to select the `location` prompt. The design
is partly templated, not a population sample or universal privacy benchmark.

Freeze commit precedes the first evaluation/result commit. The corpus, model
revision, prompt and threshold were NOT changed or optimized against this result.
Current model remains `ba967ee030b7e679dd5422cb16fcd1b379da38e1`, CITY=`location`,
ORGANIZATION=`organization`, threshold 0.5. No comparison/tuning was performed on
this holdout. The old 40/10 suites remain development sets.

[first immutable result](anonymization-evaluation/gate03c-holdout-first.json) and
[first-run command/source provenance](anonymization-evaluation/gate03c-holdout-first.provenance.json)
record the initial failure, including that measurement helpers were uncommitted
and their exact source hashes. Later opt-in verification repeats the same fixed
control assertion; it remains an actual FAIL, not xfail or a successful benchmark.

| Category | Precision | Recall | F1 | FP | FN | Unmasked canaries | Partial canaries | Covered/total alphanumeric chars |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PERSON | 0.917 | 1.000 | 0.957 | 12 | 0 | 0 | 0 | 1727/1727 |
| CITY | 0.875 | 1.000 | 0.933 | 12 | 0 | 0 | 0 | 588/588 |
| RELIGION | 1.000 | 0.944 | 0.971 | 0 | 2 | 2 | 0 | 330/358 |
| EMAIL | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 | 0 | 648/648 |
| PHONE | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 | 0 | 264/264 |
| ORGANIZATION | 0.875 | 1.000 | 0.933 | 12 | 0 | 0 | 0 | 1631/1631 |

Document exact match: **88.33%**. The two RELIGION
canaries are fully uncovered, not merely a strict-label mismatch. Overall release
qualification: **FAILED**. No finite zero-canary test would imply universal safety.

Holdout load: **26.27 s**; peak RSS **3018.29 MiB**;
warm median **122.45 ms**, maximum **472.98 ms**.
Exact NER-only and combined span/label metrics, coverage and groups are in JSON.

| Group | Documents | Exact matches | Entity false negatives |
| --- | ---: | ---: | ---: |
| de | 60 | 58 | 2 |
| en | 60 | 48 | 0 |
| ocr_noise | 24 | 24 | 0 |

## Gate 06 — commands, UTF-8 and regression results

[Final recorded commands](anonymization-evaluation/gate03c-postexport-command-results.json)
include exact argv, UTC timestamps, Python/environment overrides, source SHA, exit
code and summary for every final qualification command. The
[initial command record](anonymization-evaluation/gate03c-command-results.json)
is preserved at source 5653ab7 before the additional post-export revocation fix.

| Final command group | Result |
| --- | --- |
| ruff | exit 0: All checks passed! |
| format | exit 0: 77 files already formatted |
| default | exit 0: 208 passed, 9 skipped, 1 warning in 8.90s |
| security | exit 0: 78 passed in 0.76s |
| offline | exit 0: 18 passed in 0.10s |
| actual-model | exit 1: 1 failed, 2 passed in 50.82s |

Default pytest skips comprise three opt-in actual-model tests (including the fresh
holdout), three legacy optional/unavailable-NLTK characterization cases and three
existing live OCR/backend cases. One existing Starlette TestClient warning remains.
All actual-model tests were separately enabled: **two passed, one failed**. The old
50-case gate passes; the new 120-case canary assertion fails. This is intentionally
reported as NO-GO, not described as a successful release benchmark.

Fallback decoding now rejects incomplete/invalid UTF-8 rather than silently using
`errors=ignore`. Explicit token truncation returns a whole-codepoint prefix or suffix
within the requested token budget. Every boundary of ASCII, German accents, CJK,
emoji and Hindi samples is tested, including encode/decode round trips and the real
conversation-history suffix-truncation function. Generators are materialized before
validation so validation cannot consume and silently empty the decoded input.
No grapheme-cluster preservation guarantee is asserted; the gate concerns UTF-8.

Live components: pinned CPU model, actual SQLite files/processes, process kills,
filesystem permissions/fsync/atomic operations and isolated Docker resource limits.
Synthetic/mocked components: identity sessions, completed-review/candidate/export
stores, ACLs and operator destination approval. No real sensitive document, real
provider integration, production restart/deployment or external OCR request is
claimed. HF and original Augmentoolkit Dockerfiles/runtime packaging are unchanged.

## Independent decisions and remaining blockers

| Decision | Status | Reason |
| --- | --- | --- |
| Offline private synthetic testing | **GO** | Local boundary/UTF-8/storage regressions pass; model misses remain visible and release stays manual/disabled. |
| Private deployment | **NO-GO** | Manifest/rollback prepared, but real host/worker/provider bindings, encrypted persistent volume and maximal-load/physical destination checks remain unqualified. |
| Real sensitive-document processing | **NO-GO** | Real identity/immutable review/ACL integration BLOCKED; fresh holdout contains unmasked religion canaries. No source declassification is implied. |
| Automated release | **NO-GO** | `automated_release_eligible=false` remains invariant; no scheduler, automatic exporter or remote transmission enabled. |

Remaining work requires a separately authorized real private-host/identity adapter,
authoritative review/export stores and revocation policy, approved physical local
sinks, production-grade persistent volume/backup/key-rotation procedures, actual
host/maximal-document load tests, and recognition work evaluated against a NEW
holdout rather than tuning this frozen evidence. A real shared ledger integration
would require separate qualification for any shared-key multi-host deployment.
Do not merge, deploy, enable the service template or activate automated exports.
