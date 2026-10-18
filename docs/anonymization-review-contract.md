# Private review and candidate-provenance contract (Gate03e)

This is an in-process private-host interface, not a public API, UI or identity
provider. Production identity/review/application integration is **BLOCKED**.
Review material and original pages remain classified. None of these objects can
be passed to ReleaseAuthority as a release candidate or approval.

## Configuration commitment and candidate versions

New `AnonymizationEngine.handoff()` results use
`ocr-anonymization-candidate/v2`. The required typed `provenance` contains:

- `recognition-provenance/v1`, pinned model revision and configuration SHA-256.
- Literal terminology, status, replacement and normalization policy versions.
- No raw identifiers, custom terminology strings or entity prompts.

`provenance.py` hashes canonical UTF-8 JSON with sorted keys, compact separators,
ASCII escapes and finite numbers only. It commits the actual model revision,
float recognition threshold, complete entity prompts, normalized/sorted terminology
and version, status grammar and OCR policy (including terms, confusable classes,
length/substitution bounds, regex flags and word boundaries), replacement version
and placeholders, contact policy version, line-wrap regex and source-coordinate
mapping/case-fold configuration. Semantically reordered dictionaries/term lists do
not change the digest; threshold/prompts/terms/policy changes do. Model revision in
the candidate and provenance must agree. Missing recognizer configuration fails;
there is no guessed/default configuration for an unknown recognizer. Configuration
drift during a handoff blocks candidate creation.

The host must retain the complete private operator configuration and code/version
manifest keyed by this digest. The hash is a commitment, not a way to recover that
configuration, prove detection quality or attest an untrusted client's model run.
Only the trusted local producer registers authoritative candidate snapshots. Do
not accept caller-supplied provenance as proof of a production recognition run.

The full typed provenance participates in the candidate's existing release digest.
An approval binds that exact digest; changing provenance invalidates reuse for the
changed candidate. `ocr-review-approval/v2` and `ocr-reviewed-export/v2` remain
unchanged. Export JSON exposes only the candidate digest and existing allowlisted
fields, not the configuration manifest, source text, spans or private review data.
All candidates still have `anonymous=false`, `release_approved=false`,
`release_review_required=true`, `automated_release_eligible=false` and original
classification. Successful review display, model tests and digest equality do not
constitute release permission.

## Explicit compatibility and migration

Use `parse_candidate(serialized_json)` for version dispatch:

| Input | Behavior |
| --- | --- |
| Valid original v1 | `LegacyReleaseCandidate`; original serialization/digest preserved. No provenance is invented. |
| Valid v2 | `ReleaseCandidate`; required provenance is validated and digest-bound. |
| Missing/unknown version, missing v2 provenance, model mismatch | Constant fail-closed error; no approval is issued or consumed. |
| New normalization metadata in a v1 payload | Rejected; never disguise updated processing as the old schema. |

A v1 golden fixture was generated using the actual reviewed Gate03d schema at
`ad9a000`; its digest remains
`eb3ab0d101360654f07aa4c614f12873646d44e1d21b1e6cf096033225dec435`.
A regression approves that original v1 object, rejects reuse for a new v2 candidate,
and then successfully exports the original using the same still-unconsumed approval.
Existing legitimate v1 approvals remain valid for their original object under
unchanged TTL, current verified identity/review/ACL/destination and ledger rules.
No signing key, ledger, nonce, signature format or stored approval is rewritten.

A legacy record is displayed with `legacy_configuration_unknown`, never populated
with today's configuration. Reprocessing creates a new v2 snapshot requiring a
new completed review and approval; do not cast it to v1 or copy an old approval.
Older application versions cannot read v2; on rollback quarantine those objects
and associated approvals instead of coercing or deleting them. Preserve the ledger
and key-domain rules; restoring an old ledger with a live signing key enables replay
and is prohibited. No migration, rollback or deployment was performed here.

## Typed review interface

`PrivateReviewHost` extends the existing authoritative host security protocol.
`PrivateReviewBridge` requires a host; no identity callback or allow-all default is
provided. Credentials are opaque authentication evidence from server context,
never a requested subject or `approved=true` flag.

| Method/type | Required meaning |
| --- | --- |
| `review_binding(review_ref)` | Immutable binding to operator private environment and classification, with live revocation/expiration. Reference is an opaque ID, not a URL/path; malformed caller references are rejected before host lookup. |
| `private_environment(environment_id)` | Operator registry attests this environment is approved and private. No caller-selected destination. |
| `current_acl(identity, 'anonymization:review', sensitivity, environment_id)` | Current permission checked before loading candidate text and again before returning it. This scope grants neither export nor transfer. |
| `load_review_material(review_ref)` | Authoritative immutable snapshot, not request-provided content. Revalidated after retrieval; candidate must exist in the authoritative candidate store. |
| `PrivateReviewMaterial` | Classified candidate text/tags, derived placeholder categories, source references, uncertainty flags, blocking reason, exact candidate/evidence digests and provenance. |
| `SourcePageReference` | Opaque evidence reference, page index, original classification, original UTF-8 SHA-256 and character count. Must match review classification; candidate source spans must fit the declared original page. |
| `read_classified_page(evidence_ref, page_index)` | Private immutable original-page lookup only; no arbitrary path/URL resolution, logging, redirects or network fallback. The host must attest its evidence-store classification and snapshot binding. |
| `source_page(credential, review_ref, page_index)` | Reauthorizes, verifies returned original bytes/hash/strict UTF-8/character count, then reauthorizes before returning a classified page view. No source text in ordinary metadata; its repr hides text. |

The review environment must render complete original evidence and the candidate
side by side, plus detected categories and coordinate references, uncertainty and
safe-blocking reasons, and model/configuration provenance. Inspect whole pages,
not just detected spans: the remaining organization miss is outside a mask.
The immutable completed-review store must bind the exact shown evidence digest,
candidate digest and classification to the authenticated reviewer and destination,
then enforce completion/revocation/expiry through the existing host bridge.
This display contract does not implement a completion provider or infer completion
from a page read. It must not accept request booleans as review evidence.

For blocked jobs there is no candidate/digest and a required typed blocking reason:
`conflicting_spans`, `model_runtime_failure` or `other_incomplete`. Provenance is the
attempted configuration, not a claim of successful inference. The original can be
inspected in the private environment for diagnosis/reprocessing, with
`processing_blocked`; it cannot be exported as a nonexistent successful candidate.

Candidate/tag uncertainty remains visible, including uncalibrated scores, possible
missing entities, empty model predictions and OCR-status normalization. A category
RELIGION protects a textual term; it never asserts a nearby person's beliefs.
A finite zero-canary test is not an anonymity guarantee. Review objects and source
references are private data: do not log/serialize them into public metadata or
export JSON. Authorized views cannot retract already displayed bytes; the host
must clear cached views when sessions expire/revoke and enforce private retention.

## What is actually implemented/tested

The synthetic host adapter exists only in tests and declares `production_ready=False`.
Tests cover authorization before candidate retrieval, invalid request identity,
current ACL, revoked/expired sessions and review bindings, revocation during loads,
missing candidates, candidate/evidence/classification mismatches, source hashes,
Unicode preservation, URL rejection and sanitized errors. Original retrieval is
real bytes from isolated synthetic storage, not a production storage integration.
Existing identity/immutable review/SQLite/destination/atomic-file protections are
preserved and regressions run. Production IdP, source store, review UI, private
host worker and operational retention remain unimplemented/unconnected here.
