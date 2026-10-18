# Gate03h — candidate page and original-span validation remediation

Both reported validation gaps are **FIXED in internal verification**, ready for a
focused independent re-review. PR #3 stays draft. This agent participated in the
implementation and earlier internal review; none of these results constitute
independent security approval or human authorization to merge/deploy.

Reviewed head: `8b0aaf4ee0d22db977db600d24b96785aeca6305`.
Integration remained `281c84172281a2294227f15287a4aded8a7ea948` on
`jules-agent-0-deployment-9610133099829518547`. Feature/remote matched the reviewed
head; no rebase, force push or other-worktree edits were needed. Fix/verification
source is `7ad917e` (full SHA in command results); delivery adds evidence/docs only.

## Reproduction and root causes

The original internal review files were available. The original PoCs were rerun
against the unmodified reviewed implementation: **2 failed**, because neither
rejection assertion raised. See [exact reproduction](anonymization-evaluation/gate03h-original-reproduction.json)
and [complete synthetic failure output](anonymization-evaluation/gate03h-pocs-before.txt).
The original source is preserved byte-for-byte as
`ocr/tests/fixtures/gate03h_review_pocs.py.txt`, SHA-256
`de869531a2276c99da4cabbd23ab2bf19c09a1e2116a759d1f70f8fb677b3b2c`.
The files outside the implementation worktree were not changed.

**IR-001 — duplicate/omitted page (Medium):** candidate schemas did not require unique
page indices. Private material checked page counts and per-page membership but not
set equality: candidate `[0,0]` passed against source `[0,1]`, omitting page 1.
This was a library integrity defect for malformed imported/stored artifacts, not a
PoC that bypassed authentication, authoritative registration or signed approval.

**IR-002 — impossible replacement offsets (Low):** `Replacement.start/end` were only
strict integers, without nonnegative/positive/order constraints. The parser accepted
negative/reversed bounds. Source-aware review already rejected those particular
bounds, but common parsing/digest validation did not enforce them.

## Exact runtime changes

Only two runtime files change:

| File / boundary | Change |
| --- | --- |
| `ocr/anonymization/schemas.py` — `Replacement` | Strict integer `start >= 0`, `end > 0` and validator `start < end`. |
| Same file — shared `LegacyReleaseCandidate` base | Require at least one page, unique page indices and page/document classification consistency. Validators are inherited by v2/v3 and execute on construction, JSON parsing and instance revalidation. |
| `ocr/anonymization/private_review.py` — `PrivateReviewMaterial` | Existing cardinality check now also requires exact equality of source/candidate page-index sets. Together with uniqueness, every original page appears exactly once and no unexpected page is accepted. |

Existing source-aware checks still enforce `0 <= start < end <= original.characters`.
No source length field is added to candidates. Without immutable source references,
the parser can validate lower/order/type constraints but cannot infer an upper bound.
Original-source offsets are **not** checked against shorter anonymized text length.
Valid Unicode source codepoints remain valid even when UTF-8 byte length differs.
No new contiguous numbering/order rule is imposed on legacy archives: zero-based,
one-based and unique sparse legacy indices remain structurally supported. Exact
coverage is enforced whenever actual original-source references exist.

No release/correction policy, identity provider, signed format, model, recognizer,
normalizer, deployment packaging, service or remote backend is changed.

## Regression coverage and rejection paths

`ocr/tests/test_gate03h_candidate_integrity.py` adds **91** regular regression cases:

- Both original PoC functions execute from the byte-identical fixture. The original
  duplicate PoC parsed before entering its `raises` block; common schema validation
  now rejects it earlier. The harness accepts only that precise sanitized parser
  rejection for that original function. It does not catch failed assertions, xfail
  either test, or relax the requirement to reject the malformed artifact.
- Duplicate page 0 or 1 and empty documents reject through direct construction,
  JSON parsing, typed-instance revalidation and release digest validation, in v1/v2/v3.
- Unique but missing/foreign/additional pages reject against original source references
  in typed and serialized private review. Valid sparse legacy indices are not
  rejected merely for being noncontiguous.
- Negative/zero/equal/reversed offsets and strings/bools/floats/null reject through
  strict replacement/candidate construction, raw JSON and typed release-digest paths.
  Malformed JSON is built directly to retain its invalid type; Pydantic serialization
  of an intentionally invalid copied instance can itself coerce bools into integers.
- Source-aware upper bounds reject; valid Unicode original offsets longer than the
  shortened output remain accepted. Existing correction tests exercise fresh zero-/
  one-based multipage correction workflows, including Unicode originals.
- Bad typed pages, spans or classification cannot receive approval or export. Tests
  prove release fails before the review callback and leaves the original approval
  nonce usable; legitimate output remains classified and single-use.
- Even a malformed artifact deliberately registered in the isolated fixture is
  rejected by private view/correction inspection and correction application before
  private context loading/publication. V3 version coercion cannot bypass validation.

`parse_candidate` already maps schema errors to `unsupported_or_invalid_candidate`.
`digest` already revalidates the exact candidate type and maps errors to
`invalid_release_candidate`; approval/export call it before policy/review/consumption.
`PrivateReviewBridge` and `CorrectionService` already revalidate nested private
material. Reusing these boundaries avoids divergent validators or new permissive paths.

## Valid compatibility and historical preservation

Six structural compatibility fixtures were generated using the **unmodified**
read-only reviewed checkout and a synthetic test recognizer: v1/v2/v3 × zero-/one-based
pages. `ocr/tests/fixtures/gate03h_valid_candidates.json` stores their exact pre-fix
candidate/private-material JSON and candidate digest. Current parsing/reserialization
preserves all six byte-for-byte. These are structural test fixtures, not model
predictions, a holdout or completed production-review evidence; recorded timestamps
are historical, not live authorization credentials.

The original v1 golden fixture and digest remain unchanged:
`eb3ab0d101360654f07aa4c614f12873646d44e1d21b1e6cf096033225dec435`.
Existing approval-v2 signatures, explicit legacy/correction eligibility and SoD rules,
classification, correction provenance/history and durable nonce/replay behavior remain
covered by their original regressions. No stored candidate, review, signature, key or
ledger is migrated/reset. Valid serialized candidates do not acquire new fields.

**Fail-closed compatibility change:** previously parsable malformed artifacts with
empty/duplicate pages, mismatched classification or impossible typed offsets now
reject. Known-source page omissions/foreign pages also reject. Quarantine and investigate
invalid stored records; never silently deduplicate, invent missing pages, reclassify,
repair old signatures or erase consumed approvals. This gate does not inventory or
rewrite any production store.

[Preservation evidence](anonymization-evaluation/gate03h-preservation.json) compares all
**50** existing evaluation/corpus/golden files with the reviewed commit and records
identical SHA-256 values. The hard-pinned historical verification also passes. Old
failed holdouts, development reports and their gold source spans remain untouched.

## Actual verification at the fixed source

[Command record](anonymization-evaluation/gate03h-command-results.json) contains exact
argv/environment overrides, source SHA, times, exit codes and log hashes. Selections
overlap; their totals must not be added.

| Check | Actual result |
| --- | --- |
| Ruff check / format | PASS; 97 files formatted |
| Full lightweight suite | **478 passed, 13 skipped**, one existing Starlette warning |
| Original PoC functions via precise earlier-rejection harness | **2 passed, 89 deselected** |
| New validation regression file | **91 passed** |
| Existing Gate03g adversarial regressions | **21 passed** |
| Security/correction/release selection | **321 passed** |
| Candidate-version/provenance/compatibility selection | **128 passed** |
| Recorded quality/correction evidence | **2 failed, 1 passed, 4 skipped**, as before |

The last command deliberately retains Gate03d first-result and Gate03e remaining
uncorrected canary failures; it has exit code 1 and is not a successful recognition
qualification. The passing recorded manual-correction evidence does not count human
edits as model true positives. Four older inference tests are not rerun. Default
skips retain the existing seven opt-in anonymization checks, three optional NLTK
characterizations and three live OCR/backend checks. No failure was changed to xfail
or removed to make the result green.

No new inference, model optimization, recognition evaluation, benchmark or holdout
was performed. New fixtures concern structural validation only. Identity, completed
review, ACL and correction publication stores remain isolated synthetic adapters;
SQLite consumption and private-file operations in the existing suites use real files.
Real provider/durable correction/host-atomicity integration remains unqualified.

## Recommendations and focused re-review handoff

| Recommendation | Result |
| --- | --- |
| Finding 1 — duplicate/omitted pages | **FIXED**, internal tests; focused independent confirmation requested |
| Finding 2 — invalid replacement bounds | **FIXED**, internal tests; focused independent confirmation requested |
| Development-branch merge | **GO recommendation subject to focused independent re-review**, not approval/merge authorization |
| Real private-host integration | **NO-GO** |
| Sensitive-document processing | **NO-GO** |
| Automated release | **NO-GO**, `automated_release_eligible=false` unchanged |

Return to the reviewer with these two runtime diffs, the byte-identical PoCs, pre-fix
failures, all-version regressions, old compatibility bytes and exact-head CI. Recheck
source-page bijection, original-coordinate bounds, instance revalidation and release/
correction call paths. Verify the original v1 digest and all Gate03g guards remain
intact. A reviewer must be genuinely distinct from the implementation participant;
this report is not independent approval. PR #3 stays draft/open. Do not merge, deploy,
activate HF services, enable remote processing or authorize automatic exports.
