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
qualifies only the supplied German/English synthetic corpora.

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
ORGANIZATION. Prompt descriptions and bracketed placeholders are configurable;
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

`ReleaseAuthority` requires a secret of at least 32 bytes and an application-supplied
authorizer. It checks the reviewer, scope `anonymization:release`, exact candidate
digest and source sensitivity, plus an explicit completed review. A 15-minute HMAC
approval binds the exact serialized candidate. Export rejects missing, modified,
expired or forged approvals. The host application must authenticate the actor and
perform genuine review; a permissive example authorizer is not an identity system.
No secret or approval is generated from document text. This is a local API, not a
new remotely accessible release service. The key remains in private settings.

Approval preserves D0/D1/D2 and does not assert complete anonymity. D1/D2 exports
require the classified JSON container; bare text exports are restricted to D0.
Declassification is not implemented. There is no automatic release path, even when
a synthetic test passes. Reviewers must examine remaining identifiers in addition
to detected replacements.
