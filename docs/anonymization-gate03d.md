# Gate 03d — religious terminology and independent qualification

**Offline private synthetic testing: GO. Model recognition qualification: NO-GO.
Private deployment, real sensitive-document processing and automatic release:
NO-GO.** This delivery improves two development misses; the new independent
qualification FAILS. It is not a successful release benchmark.

## Synchronization and evidence chain

- Reviewed starting head: `4c5cbbc30801f5911dcf52e1997a70404026d425`.
- All remote heads fetched. Integration base remains
  `281c84172281a2294227f15287a4aded8a7ea948`; ancestry verified, no rebase needed.
- Existing feature worktree `/workspace/augmentoolkit-anonymization`, branch
  `feat/ocr-anonymization-failclosed`, draft PR #3. Other worktrees preserved.
  No reset, force push, merge, deployment or production service modification.
- Selected implementation and new corpus/configuration frozen at
  `d6dac617d0d62c8d5356cc0ce351b837a4ef0ec2`, before first new-corpus inference.
- First failed result committed at
  `4f71af4e6043ba4979abb72f5df66f69b4d8e5e6` before further interpretation.
- Final resource probe and lightweight verification source:
  `dde2cda9284c9f95f246423d9cbd0c6ca0f61d69`.
  Recognition/evaluation files remain byte-identical to their frozen hashes.

Gate 03c's cases, freeze, first result and first provenance are byte-for-byte
unchanged from the reviewed head. Its failed first result remains historical
independent evidence. The 40+10+120 cases are now **development** data, not an
untouched test for this fix. See [Gate03c](anonymization-gate03c.md).

## Confirmed diagnosis

[Actual synthetic predictions, scores, normalized text and tokenizer offsets](anonymization-evaluation/gate03d-diagnosis.json)
confirm exactly two uncovered RELIGION spans:

| Case | Source slice, code-point offsets | Genuine score at diagnostic threshold 0.01 | Meaning |
| --- | --- | --- | --- |
| `holdout03c-102` | `konfessionslos`, `[22,36)` | 0.48521217703819275 | Explicit personal non-affiliation |
| `holdout03c-108` | `konfessionslos`, `[10,24)` | 0.08470416069030762 | General topic, with explicit denial of personal attribution |

At the unchanged production recognition threshold 0.5 neither span is predicted.
For 102, PERSON/EMAIL/PHONE predictions have genuine scores 0.998397/0.891172/0.994336.
For 108, ORGANIZATION/PERSON have 0.955028/0.998249. None covers the religion span.
Normalization is identity in both cases; every original code point maps back
unchanged. Tokenization retains `▁kon`, `f`, `essions`, `los`, with normalized
positions approximately `[21,25),[25,26),[26,33),[33,36)` and
`[9,13),[13,14),[14,21),[21,24)` respectively (leading whitespace belongs to the
first tokenizer offset). There is no unknown token, truncation or OCR spelling
variant. The model represents the exact span at low diagnostic threshold but
scores it below the operating threshold, especially in the topic context.
This is contextual confidence/semantic scope failure, not source-coordinate loss.
Low-threshold scores diagnose the failure; they are not calibrated privacy odds.

## Fixed development comparison and selection

Pinned control remains
`VAGOsolutions/SauerkrautLM-GLiNER@ba967ee030b7e679dd5422cb16fcd1b379da38e1`,
CPU, two inference threads, threshold 0.5. Prompt remains `religion`; other prompts
are unchanged, including CITY=`location`.

Three configurations were fixed before the comparison: control plus existing
contact rules; semantic prompt `religion or religious non-affiliation` plus contact
rules; and unchanged model plus bounded EN/DE religious terminology and contacts.
No threshold sweep or case/context-specific runtime rule was added.
The [full comparison](anonymization-evaluation/gate03d-development-comparison.json)
contains every category's exact metrics, timings and per-case before/after
predictions, including regressions. The [execution provenance and reconstruction
patch](anonymization-evaluation/gate03d-development-provenance.json) distinguish
uncommitted experiment source from the subsequently frozen implementation.

| Configuration | Development suite | RELIGION P / R / F1 | FP / FN | Religion canaries | Document exact match |
| --- | --- | --- | --- | --- | --- |
| control | qualification (40) | 1.000000 / 1.000000 / 1.000000 | 0 / 0 | 0 | 87.500000% |
| control | validation (10) | 0.666667 / 1.000000 / 0.800000 | 1 / 0 | 0 | 80.000000% |
| control | development03c (120) | 1.000000 / 0.944444 / 0.971429 | 0 / 2 | 2 | 88.333333% |
| semantic_status_prompt | qualification (40) | 0.000000 / 0.000000 / 0.000000 | 0 / 6 | 6 | 75.000000% |
| semantic_status_prompt | validation (10) | 1.000000 / 0.500000 / 0.666667 | 0 / 1 | 1 | 80.000000% |
| semantic_status_prompt | development03c (120) | 1.000000 / 0.444444 / 0.615385 | 0 / 20 | 20 | 73.333333% |
| hybrid_terminology | qualification (40) | 1.000000 / 1.000000 / 1.000000 | 0 / 0 | 0 | 87.500000% |
| hybrid_terminology | validation (10) | 0.666667 / 1.000000 / 0.800000 | 1 / 0 | 0 | 80.000000% |
| hybrid_terminology | development03c (120) | 0.750000 / 1.000000 / 0.857143 | 12 / 0 | 0 | 90.000000% |

Selected: the **hybrid terminology** configuration for reviewed private testing.
The semantic prompt regresses severely and is rejected. The hybrid fixes both
original misses without changing model weights, prompts or threshold. The other
five categories' aggregate metrics do not change on these development sets.
This choice is conservative text protection, not an overall precision improvement:
12 existing 03c generic negative controls containing “no religious affiliation”
now receive a RELIGION false positive. Their IDs are `009,019,...,119`.
They were already inexact because the model tags generic person/place/organization
words; no previously exact development document becomes inexact. Exact matches
improve from 106/120 to 108/120, while religion F1 falls from 0.971429 to 0.857143.
No negative gold label was changed to hide these false positives.

`ReligiousTerminology` accepts at most 256 operator-configured literal terms of
80 code points each, with a 2M-code-point text bound and 100k-match cap. It accepts
no regular expressions or caller request configuration. Unicode word boundaries
include combining marks/underscores; case folding and line-wrap dehyphenation map
to exact original slices. No partial case-fold expansion is accepted. Scores are
`None` with deterministic `religious-terminology-v1` provenance. Operator custom
term lists must be recorded as policy configuration in the private audit store;
the version identifies the algorithm, not arbitrary custom policy contents.

Negations, quotations and general religious topics are protected as text.
No personal affiliation or person-to-religion relationship is inferred.
Ambiguous adjectives such as orthodox/evangelical are left to contextual GLiNER.
The finite vocabulary is not exhaustive and does not perform OCR spelling repair.
Engine model errors still block; rules never replace an unavailable/failed model.
Overlaps still fail closed rather than discarding inconvenient conflicting evidence.

## New independent first result — FAILED

The new [frozen corpus](../ocr/anonymization/fixtures/holdout03d/README.md)
has 160 individually authored synthetic cases, 80 DE/80 EN, ten strata, no repeated
03c entity-set templates. It includes affiliation, non-affiliation, beliefs,
nonpersonal topics, negations, quotations, ambiguous adjectives, OCR corruption,
multiple/repeated entities, varied formats and unrelated negatives. Taxonomy and
exact source slices were frozen before inference. This is a new synthetic authored
sample, not an externally blinded study or representative privacy guarantee.

Corpus SHA-256: `7180e176f6d8bd259da56ad729ede6fd2c798aca15a786ba0ff0adcd862f21a1`.

The selected implementation ran once on this corpus; the subsequent acceptance
test reads the immutable result and does not repeat inference. No new-corpus-driven
model, prompt, terminology or gold change followed. The [first result](anonymization-evaluation/gate03d-holdout-first.json)
and [provenance with unblocked/blocked case separation](anonymization-evaluation/gate03d-holdout-first.provenance.json)
record exact SHAs, source hashes, UTC times, dependency versions and per-case errors.
Its first-result SHA-256 is
`08f86d2b78871683835c51b627e251c66b83fc33f828d8a6c8e9742c8bd47d9f`.

| Category | TP | FP | FN | Precision | Recall | F1 | Partial masks | Fully uncovered canaries |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PERSON | 20 | 5 | 2 | 0.800000 | 0.909091 | 0.851064 | 0 | 1 |
| CITY | 7 | 2 | 1 | 0.777778 | 0.875000 | 0.823529 | 0 | 1 |
| RELIGION | 137 | 3 | 13 | 0.978571 | 0.913333 | 0.944828 | 1 | 8 |
| EMAIL | 3 | 0 | 1 | 1.000000 | 0.750000 | 0.857143 | 0 | 1 |
| PHONE | 2 | 0 | 0 | 1.000000 | 1.000000 | 1.000000 | 0 | 0 |
| ORGANIZATION | 5 | 12 | 1 | 0.294118 | 0.833333 | 0.434783 | 1 | 0 |

Document exact match: **132/160 = 82.5%**. German 60/80; English 72/80;
OCR-corrupted 10/16. Exact-boundary false negatives can still be fully covered
by enclosing masks; canary coverage is measured separately on source alphanumerics.
The aggregate coverage gate conservatively counts blocked documents as uncovered;
it never silently removes difficult examples from evaluation.

Returned candidate misses:

- `025`: `keine Religion` is partially covered; five letters in `keine` remain.
- `026`: `religiously unaffiliated` is fully uncovered (23 alphanumerics).
- `111`: OCR `konfesslonsfrei` is fully uncovered (15 alphanumerics).
- `125`: organization `Hafenbogen Archivs` is partially covered (7 alphanumerics
  remain). The original source was not altered to repair the inflection/boundary.

Five cases (`055,063,078,127,137`) fail `ambiguous_entity_overlap`; no candidate is
returned. Their six religion canaries and one PERSON, CITY and EMAIL canary are
counted uncovered in aggregate, but are **blocked evidence, not transmitted output**.
Conflicts include model labels that disagree with a deterministic religious term;
the policy correctly refuses to pick a convenient label. These block recovery,
recognition and organization-boundary issues require further development and a
new independent evaluation before any recognition qualification claim.
No result here authorizes real sensitive processing or proves universal safety.

## Security, runtime and resources

ReleaseAuthority, real-host adapter contract, durable SQLite consumption,
destination transport, atomic output and classified-file recovery implementations
are unchanged from Gate03c. Regression tests exercise actual temporary SQLite
storage, multiprocessing/termination/restart, replay, permissions, key rotation,
ACL/review revocation, explicit destination IDs and no-overwrite output. Identity,
review and destination policies use the isolated test adapter; no real production
provider is simulated or declared integrated. See [host/staging contract and
rollback](../ocr/deploy/private-anonymization/README.md).

Candidates retain `anonymous=false`, `release_review_required=true`,
`release_approved=false`, `automated_release_eligible=false` and original D1/D2.
The original OCR evidence remains in the caller's authorized store. Production
review must let an authenticated, currently authorized reviewer inspect that exact
original evidence and the candidate within the private environment; neither
this CLI nor this PR supplies a production identity provider or review UI.
Export approval still binds the exact digest, identity, immutable review,
classification, destination and expiry and is consumed durably. An exported JSON
is not transmission permission. No model outcome or rule match authorizes release.

New independent unit tests cover case-fold expansion, combining-mark/identifier
boundaries, independent EN/DE terms, quotations/negations/topics, configurable
literal bounds, OCR source mapping, deterministic no-score provenance, D2 flags,
model-error fail-closed behavior and conflicting labels. The untouched holdout and
first-result no-overwrite guard have regression tests too.

Standalone first-model evaluation: load **11.837 s**, peak RSS
**3018.203 MiB**, median/max end-to-end case latency
**93.217/125.148 ms**.
Deterministic layer alone median/max **0.0417/0.2291 ms**,
without extra model inference. Prior03c standalone RSS was 3018.293 MiB.

The [first hybrid resource probe](anonymization-evaluation/gate03d-runtime-hybrid.json)
and [paired probe](anonymization-evaluation/gate03d-runtime-paired.json) run only in
throwaway cloud test containers: network=none, no published ports, read-only
source/model/dependencies, non-root UID, dropped capabilities, 8 GiB memory/4 vCPU,
and a simultaneous 1536 MiB physically touched CPU/memory load.
Paired load **12.672 s**; process peak **3020.609 MiB**;
cgroup peak **4495.859 MiB**, headroom **3696.141 MiB**.
Compared with prior03c cgroup peak 4495.094 MiB, this bounded sample shows no material
memory increase. Same-text control/hybrid median **128.961/129.203 ms**.
These alternate order with a hybrid readiness call before each pair. Maxima
**833.626/1068.248 ms** demonstrate scheduling/warmup variance;
no latency SLA or throughput improvement is claimed. Development03c median changed
117.991 to 149.390 ms between serial experiment arms; the paired check addresses
that observed variation without changing the selected recognition configuration.

Both containers report **zero native TCP listeners**. A single urllib3 IPv6
capability-check `socket.bind` attempt is explicitly denied by the audit hook;
no DNS or connect attempt is recorded. Network namespace isolation is additional
enforcement. Both containers were removed. No host or production service was
started/restarted or public listener created. Model initialization uses the
integrity-verified local pinned artifacts with hub/transformer offline and
telemetry disabled. The prepared staging service/manifest stay disabled and the
production identity/application entrypoints remain BLOCKED. These cloud probes
are not private-host installation, worst-case 2M-character qualification,
encryption-at-rest or power-loss durability certification.

## Verification commands and outcomes

Exact argv, paths, environment overrides, source SHAs, UTC starts and exit codes:
[initial and actual-model command record](anonymization-evaluation/gate03d-command-results.json),
[final lightweight command record](anonymization-evaluation/gate03d-final-command-results.json),
[hybrid resource command](anonymization-evaluation/gate03d-runtime-provenance.json),
[paired resource command](anonymization-evaluation/gate03d-runtime-paired-provenance.json).
Python 3.11.17, GLiNER 0.2.29, torch 2.8.0+cpu, transformers 4.57.1,
NumPy 2.4.6, Pydantic 2.14.1, Ruff 0.17.1. Locked environments were reused;
no new service/dependency setup was necessary and no environment draft changed.

| Check | Result |
| --- | --- |
| Ruff check + format check | PASS (82 Python files formatted) |
| Default OCR suite | **240 passed, 11 skipped**, one existing Starlette deprecation warning |
| Explicit security subset including OCR routing | **171 passed** |
| Offline/UTF-8/atomic-output subset | **18 passed** |
| Actual pinned-model smoke/handoff + old40/10 + selected170 development | **3 passed** |
| Historical Gate03c model-only acceptance assertion | **1 FAILED**, original two misses retained |
| New independent first-result acceptance assertion | **1 FAILED**, no xfail/relabel/changed gold |
| Whole opt-in integration invocation | **3 passed, 2 FAILED**, exit 1 |
| Standalone first holdout command | **FAILED**, report saved, exit 1 |
| Constrained hybrid and paired resource probes | PASS as bounded resource checks only |

Default skips: five explicit heavy-model/holdout gates, two optional NLTK legacy
characterizations, one opt-in legacy OCR integration, three live OCR backend tests.
These are not model qualification passes. Heavy tests remain opt-in for lightweight
CI; the strict model gates above stay red. Exact-head GitHub Actions is checked on
the pushed draft PR; CI does not run these costly pinned-model gates.

## Independent delivery decisions

| Decision | Status | Basis |
| --- | --- | --- |
| Code/security boundaries for reviewed private synthetic testing | GO | Lightweight/security suites pass; no security boundary weakened. |
| Offline private synthetic testing | GO | Pinned local inference works; first-result failures remain visible. |
| Model recognition qualification | **NO-GO** | New independent first gate fails, including genuine candidate misses and blocking conflicts. |
| Private deployment | **NO-GO** | No real host application/identity adapter qualification; staging remains disabled. |
| Real sensitive-document processing | **NO-GO** | Recognition gate fails; production identity/review integration BLOCKED. |
| Automated release | **NO-GO** | Always `automated_release_eligible=false`; no automatic exports or remote processing enabled. |

PR #3 remains draft/open. No merge, HF deployment, production modification,
remote model processing or automated export is authorized or performed.
