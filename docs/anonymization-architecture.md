# Local span anonymization architecture

The supported workflow is `anonymization_handoff(ocr_result, AnonymizationEngine(recognizer))`
or `python -m ocr.anonymization`. It creates a **classified review candidate**, not an
anonymous public document. Original `OCRResult` objects remain unchanged and must
stay in the caller's authorized evidence store. No public API or listener was added.

## Recognition and coordinates

`GLiNERRecognizer` uses the native GLiNER/PyTorch runtime with
`VAGOsolutions/SauerkrautLM-GLiNER@ba967ee030b7e679dd5422cb16fcd1b379da38e1`.
The model card declares MIT and training in English, German, French, Italian and
Spanish, using the multilingual ModernBERT/mmBERT encoder. This implementation
evaluates supplied German/English synthetic corpora; independent Gate03d
recognition qualification fails (see the linked current report).

Candidates considered:

| Candidate | Evidence and decision |
| --- | --- |
| VAGOsolutions/SauerkrautLM-GLiNER | MIT, explicitly German/English, configurable span labels, local CPU-compatible native runtime; selected and actually evaluated. |
| urchade/gliner_multi-v2.1, revision 443d26d654e0324125a96bebd8e796c14ff2efe6 | Apache-2.0 multilingual DeBERTa GLiNER alternative; metadata/model card inspected. Not downloaded or benchmarked. VAGO's published comparisons informed selection but are not our measured results. |

The selected checkpoint is about 1.3 GB; measured process peak is about 3 GiB.
It belongs on an authorized private host with sufficient memory, outside the HF
CPU OCR worker. No llama.cpp integration is used. CPU is tested; CUDA is an
explicit operator choice requiring a separately qualified GPU wheel/environment.

All six categories are mandatory: PERSON, CITY, RELIGION, EMAIL, PHONE and
ORGANIZATION. The CITY privacy label now uses the general `location` prompt, including cities,
states and named geographic places as represented in the unchanged qualification
corpus. This intentionally masks some non-city places too; CITY is a privacy
placeholder category, not a claim that each place is administratively a city.
Prompt descriptions and bracketed placeholders are configurable;
missing/unknown categories, duplicate prompt descriptions and invalid placeholders
block processing. Model scores are retained only as model scores, not calibrated
privacy guarantees. Contact regexes have **no invented score** and are conservative
supplements, not complete email/phone parsers. Phone-like dates can be false positives.
Religion labels describe explicit religious terms/affiliation in text; no name,
city, demographic or person-to-faith lookup exists. The model can still mislabel
ambiguous language, so a label does not assert that a person has an affiliation.

The recognizer processes overlapping 128-whitespace-token windows with stride 96.
Every token is covered, original offsets are restored, and long tokens/encoder
inputs are rejected instead of silently truncated. It serializes inference with a
lock. The only normalization removes ASCII hyphen plus line break **between letters**.
The normalized view has a codepoint-to-original-codepoint map; predictions map
back to slices that include the original hyphen/newline. `normalization` records
that view. Ordinary hyphenated names and umlauts remain intact. No spelling repair,
transliteration or fuzzy name dictionary is applied. Unsupported OCR noise can be
missed and is part of the qualification/review limitation.

## Validation and replacement

`Entity` is private evidence: start/end, required label, exact original text slice,
genuine optional score, fixed provenance and pinned model revision. Strict validation
rejects booleans as offsets, invalid numbers, extra fields, unknown labels and
source-slice mismatches, including forged `model_copy` values. Runtime model output
is mapped into this schema, never passed through as arbitrary metadata.

Replacement sorts by ascending start and descending end. Identical predictions
with the same label are deduplicated; enclosing spans cover nested spans. Crossing
spans and identical bounds with conflicting labels block the document because
choosing one could expose part of another identifier. Replacements run left to
right against the untouched source; unrelated punctuation, whitespace and paragraph
breaks are preserved. Repeated detected names use the same category placeholder.
No assertion is made that detecting one occurrence guarantees finding every repeat.

## Evidence, candidates and release

Only a completed, failure-free OCR result with all consecutively indexed pages and
consistent document/page classification can produce a candidate. Limits are 100
pages and 2 million text characters. A failure on any page returns no partial
candidate. Exceptions become constant `SafeError` codes, including untrusted
exception messages/codes; document contents are never logged by this implementation.

`ReleaseCandidate` is frozen, strictly typed and allowlisted. Its pages contain only
anonymized text, replacement metadata without raw entity strings, source enum,
page index and original sensitivity. Raw Markdown, structured blocks, alternatives,
job/page IDs, engine/provider strings, previews and exceptions are **not copied**.
Exports are rebuilt from this candidate. Original OCR provenance stays in the
separate classified evidence object. Unknown fields are rejected.

All candidates have `anonymous=false`, `release_review_required=true`,
`release_approved=false`, `automated_release_eligible=false`. Empty predictions add
an explicit review reason and never imply successful recognition. Candidates can
still contain missed sensitive information; they remain classified and private.

`ReleaseAuthority` now requires a secret of at least 32 bytes, an application
review verifier, an ACL authorizer, an atomic replay consumer, and an operator-owned
destination registry. None has a permissive default. The old `review_completed=True`
boolean and unscoped v1 approvals are no longer accepted.

The review verifier must authenticate the actor/session and load an already
completed immutable review record from the private application's authoritative
store. It returns `VerifiedReview` with opaque verified reviewer subject, review
record ID and evidence digest, exact candidate digest, source sensitivity,
destination ID and completion timestamp. The application must verify who reviewed
what and that the review is complete; this library does not invent identity or
review evidence. Simply echoing request fields is an invalid integration.

The ACL callback checks that verified subject, `anonymization:release` scope and
configured destination. Both verification and authorization run at issuance and
again at export, so revoked access or changed review evidence fails closed.

V2 approvals sign every field: random approval ID, candidate digest, full review
attestation, destination registry snapshot, output format, issuance and expiration.
They expire at most 900 seconds after review completion (configurable shorter TTL).
Future/stale review timestamps, wrong actors, altered candidates/destinations/formats
and expired signatures are rejected. Approval must be consumed exactly once through
persistent atomic replay storage before bytes are returned. `SQLiteApprovalLedger`
is an optional local implementation, tested across authority instances and racing
threads; the host may supply an equivalent durable atomic store. All instances
sharing a signing key must share the same ledger. Losing replay state requires key
rotation before accepting approvals again. A crash after consumption burns the
approval; reissuance requires fresh authentication/authorization and valid review.

Destinations are registry IDs with an approved private/public class and explicit
sensitivity allowlist. Unknown IDs, changed registry entries and every D1/D2 public
class are denied even if a host ACL callback incorrectly allows them. D1/D2 require
JSON, with classification and destination retained. Export explicitly states
`transmission_authorized=false`. A JSON release is not a transport permission.
The private application's separate destination enforcement must resolve the ID to
the approved physical sink, verify it remains approved for the class, and forbid
caller-supplied URLs or external rerouting. No transport, remote processing or
public release endpoint is added here. Physical destination attestation remains a
production application responsibility; labeling a remote endpoint private is not
such attestation.

Approval preserves D0/D1/D2 and does not assert complete anonymity. Declassification
is not implemented. `automated_release_eligible` stays false for every candidate,
even when synthetic tests pass. Reviewers must examine remaining identifiers in
addition to detected replacements. See [PR #3 hardening](anonymization-pr3-hardening.md)
for exact synthetic diagnosis, comparison evidence and separate readiness decisions.


## Gate 03c integration hardening

`host_adapter.py` defines the concrete host integration protocol and validation
bridge: authenticate opaque credentials, retrieve immutable completed review
evidence, validate authoritative candidate/export digests, recheck expiry,
revocation, ACLs and live destination policies. A concrete production provider is
not supplied or configured; the isolated adapter lives only under tests. See
[Gate 03c](anonymization-gate03c.md) for blockers and actual test evidence.

SQLite now requires explicit one-time provisioning and a pinned ledger identity
on restart. Mode-rw connections never recreate missing storage; inode replacement,
corruption and unsafe file/directory permissions fail closed. Consumed IDs are
retained; automatic expiry garbage collection was removed to avoid replay after
clock rollback. Local multi-host ledgers must have distinct actual key fingerprints.
A claimed shared-backend flag is not accepted as shared-durability qualification.

`PrivateFileTransport` implements an explicit separately authorized local delivery
operation only. It checks an authoritative exported-artifact registry, a fresh
`anonymization:private-transfer` ACL, a fixed private destination root/inode and
the typed classified payload. It has no network client, caller paths/URLs, redirects
or public fallback. Export does not invoke it. Recovery removes only old private
staging names with exclusive locking and never promises secure erasure.

## Gate 03d recognition supplement

The engine now combines the unchanged pinned model/prompts/threshold with
`ReligiousTerminology`, a bounded, operator-configurable literal EN/DE terminology
detector. It maps case-folded/dehyphenated matches back to exact source slices and
records deterministic provenance with no confidence score. Terms, topics,
negations and quotations are protected as text, never evidence of an individual's
faith. No person-to-religion relation is inferred. Conflicting model/rule spans
still block the entire document. Operators must retain the exact custom term list
as private policy evidence; the rule version alone is not a configuration hash.

The selected hybrid covers both old religion canaries but adds measured false
positives. The new 160-case independent first qualification fails. See
[Gate03d](anonymization-gate03d.md) for exact metrics and NO-GO decisions.

## Gate03e current reporting and review boundary

The supported new handoff is candidate v2 with a canonical configuration digest;
legacy v1 remains explicitly readable without fabricated provenance. The digest
binds model revision/threshold/prompts, terminology and status/OCR policy,
replacement version/placeholders and normalization. Strict runtime errors and
configuration drift cannot create a candidate. Scoped status-phrase grammar and
single-confusable long status-word protection supplement the unchanged pinned
model. Source offsets and fail-closed overlap behavior remain unchanged.

`score_outcomes` separates emitted-candidate canaries, safe abstention and other
incomplete processing from raw recognition/all-source replacement accuracy. It
does not overwrite historical metrics. `PrivateReviewBridge` supports authorized
private candidate/original inspection with source integrity and classification
checks and live revocation/ACL rechecks; no production IdP, UI or release shortcut.
See [Gate03e](anonymization-gate03e.md), [typed review contract](anonymization-review-contract.md)
and [acceptance specification](anonymization-gate03e-acceptance.md). Recognition,
private deployment, real sensitive processing and automated release remain NO-GO.
