# Private reviewed correction contract — Gate03f

This is a private in-process contract, with an isolated synthetic host fixture.
Production identity, durable correction storage and the review UI are **unqualified**.
No public endpoint, identity provider, worker deployment or automatic exporter is added.

## Authoritative registration and inspection

A trusted local producer calls `prepare_review(engine, OCRResult, review_ref=...,
environment_id=..., expires_at=...)` once, using server/operator registration values.
It validates completed OCR, page order and classification, captures the actual pinned
NER and rule observations before overlap resolution, and commits complete original
OCR evidence, original UTF-8 page hashes and the recognition configuration. Runtime
failure aborts capture; only genuine conflicting spans become a correctable blocked
snapshot. This is not an import route for client assertions or fabricated provenance.

The host must atomically register immutable original OCR, original page bytes,
`MachineSnapshot`, `CorrectionContext`, and any original v2 candidate before making
the review reference available. The host retains all machine observations, including
ones deduplicated or enclosed by replacement. Raw observations remain classified.
The private context is never an ordinary log object or an export payload.

`CorrectionService.inspect(credential, review_ref)` requires authenticated identity,
live session, current `anonymization:review` **and** `anonymization:correction:inspect`
ACLs for the original classification and operator-approved private environment.
Authorization precedes private context loading and is checked again before return.
`PrivateReviewBridge.source_page` verifies original bytes, SHA, UTF-8 and character
count. Full pages, machine observations/candidate, blocked conflicts, effective edits,
proposals and corrected candidates must be displayed inside this private boundary.
No UI is supplied; displaying these objects never records completed release review.

## Proposal and source coordinates

`bind(credential, review_ref, page_index, start, end)` requires correction-submit
permission and creates a source-span commitment. Offsets are half-open **original
Python Unicode codepoint** indices, never transformed-text offsets or UTF-8 bytes.
The binding commits the original OCR digest, immutable page reference/classification,
page hash/length, offsets and hash of the exact original substring. It contains no
substring. Page/offset/hash validation is repeated at proposal and publication.

`CorrectionEdit` accepts only a bound span, category, action, enumerated rationale
and explicit observation IDs being superseded. It accepts no replacement text.
Masking uses the original operator placeholder map and deterministic replacement.

| Rationale | Action and evidence |
| --- | --- |
| `missing_entity` | Mask, no targets; no current overlapping observations permitted. |
| `incomplete_boundary` | Mask an original-source span, explicitly naming every overlapping observation. |
| `wrong_category` | Mask a new category; named observations must have the same original bounds. |
| `span_conflict` | Mask a reviewed original span; explicitly name all conflicting/overlapping observations. |
| `false_positive` | Suppress explicitly named same-category observations at exactly those bounds. |

All overlapping human edits, reused targets, unknown targets, stale contexts and
unresolved machine conflicts block the entire batch. No partial candidate is returned.
Suppression deliberately restores original text and therefore needs full-page review;
it does not assert safety. Gold labels exist only in synthetic demo selection, never
in the engine. No test entity, spelling or organization name is hardcoded into policy.

`propose(credential, review_ref, edits)` derives author, UUID, evidence/configuration,
prior candidate/context/machine commitments and timestamps from authenticated host
state. A proposal (`private-ocr-correction/v1`) expires within 900 seconds and no later
than its review binding. Caller-provided identity or an approval flag is not accepted.
All classified source slices and captured machine IDs are revalidated, including
observations about to be removed. Errors are constant `private_correction_denied`;
trusted capture errors are `correction_capture_failed`. No raw values are logged.
Hashes/opaque IDs are private audit data, not declassification or anonymous identities.

## Host persistence and concurrency requirements

`CorrectionHost` extends `PrivateReviewHost`. It adds metadata-only
`correction_binding(id)`, `load_correction_context`, `load_correction`,
`store_correction` and `publish_correction`. IDs must resolve within the caller's
current authorized private environment, never to caller paths or URLs. Before any
proposal payload is read, its binding is resolved and correction-inspect ACL checked.

The **real host** must implement durable transactions, not the fixture's RLock:

1. Authenticate again and validate current session/review expiry/revocation, original
   classification, environment and operation ACL **inside the commit transaction**.
2. Compare-and-swap the document's active context against `expected_context`; enforce
   unique correction IDs and immutable proposal bytes. Concurrent losing writes fail.
3. On publication check stored proposal equality, author, expiry/revocation, single-use
   state and prior head. Store the complete new context/candidate, unchanged machine
   root and parent reference, advance the head and consume the proposal atomically.
4. Retain every old context/candidate, original evidence, proposal and approval. Store
   a recoverable publication pointer containing new review ref and candidate digest.
   Retrying an already applied correction fails. If a reply is lost after commit,
   inspect the stored pointer; never automatically republish or erase the candidate.
5. Qualify persistence across crash/restart, backup/restore, corruption and concurrent
   processes before real use. The isolated fixture proves only in-process semantics.

`apply(credential, correction_id)` separately requires correction-apply permission,
current author identity and the complete validated batch. Every publication gets a
new candidate UUID/digest, new private review reference, retained original class,
recognition provenance and explicit `ocr-correction-provenance/v1`. It commits the
proposal, machine root, original evidence/references, prior candidate/effective state,
new effective state and cumulative author subject hashes. No old candidate mutates.
Machine observations and effective human observations are distinct, inspectable records.
Human replacement tags carry `human_correction`, null score and correction policy
revision; unchanged tags retain actual machine/rule provenance. Suppression is audited
in the proposal/state chain even though it produces no replacement tag.

## Approval is a separate operation

All corrected candidates retain `anonymous=false`, `release_review_required=true`,
`release_approved=false`, `automated_release_eligible=false` and uncertainty reasons.
A completed review of an old candidate or a correction proposal grants nothing for
the new candidate. Old approvals remain valid only for their original exact object
under existing expiration/revocation/destination/nonce rules; no ledger is reset.

The host must implement `corrected_release_allowed(candidate, verified_review)`;
absence, exceptions or anything other than literal `True` denies v3 approval/export.
It must verify authoritative candidate/correction history and the current separation
of duties policy, comparing final reviewer against **all** correction authors when
separation is required. There is no allow-all default or built-in production policy.
The bridge checks this policy both when approving and immediately before exporting.
The synthetic fixture explicitly opts into separate author/approver roles.

Final approval still requires independently immutable completed-review evidence bound
to authenticated reviewer, exact candidate/evidence digest, classification, approved
destination, ACL, TTL and revocation. SQLite approval consumption is separate durable
single-use state. An approved D1/D2 JSON artifact still has
`transmission_authorized=false`; actual approved private delivery needs its own
`anonymization:private-transfer` authorization and operator destination resolution.
No external transmission is added or exercised here.
