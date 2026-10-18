# Gate03e acceptance specification — recorded before development comparison

All 40+10+120+160 existing cases are development evidence. No new independent
corpus is created in this gate. Old first results and frozen inputs stay unchanged.
Model, prompts and threshold remain the pinned Gate03d control.

## Selection of a limited development improvement

Compare the previous literal hybrid, contextual status grammar, and that grammar
plus bounded one-character OCR-confusable recognition of long status words.
Select an improvement only if, on each existing suite:

- Returned-candidate partial/fully uncovered canary counts do not increase.
- No complete candidate becomes blocked and no old conflict is discarded.
- Document exact match does not decline.
- Exact-span false-positive counts do not increase.

At least one returned-canary failure must improve. Report per-case regressions
including category/boundary errors, safe abstentions and overmasking; no gold edits.
If these criteria fail, retain the previous recognition policy and report it.
This is a development selection criterion, not independent model qualification.
Any exception needs explicit rationale recorded before a future independent test.

## Separate operational gates

1. **Offline synthetic development:** GO requires passing deterministic/security,
   no-network and classified-output tests; pinned local inference works; every
   failed quality gate remains visible. Known misses are allowed only as labeled
   development failures. No real documents or automatic release.
2. **Private staging deployment:** additionally requires the actual host's model
   integrity/offline/restart checks, 8 GiB measured worker profile with at least
   25% memory headroom under representative concurrent load, no network/listeners,
   bounded queue/job/text/retention enforced, encrypted storage/cleanup/recovery,
   actual identity/review/ACL and destination adapters, and ledger/key-domain tests.
   These host requirements remain BLOCKED here; cloud samples cannot qualify them.
3. **Manually reviewed sensitive processing:** additionally requires complete
   original-evidence and candidate display within current authorized private
   sessions; immutable digest/classification/configuration bindings; verified
   reviewer completion, revocation/expiry/replay and separate destination permission.
   Before proposing a fresh independent corpus, document a recognition target:
   zero partially/fully uncovered source canaries in returned candidates; at least
   98% candidate availability; per-category source-span recall >=98%, precision
   >=95%; document exact match >=95% across declared language/noise strata.
   Abstentions stay failures of availability, never successful anonymizations.
   No review of a blocked job can release a nonexistent candidate. Human review
   must examine complete pages and fix/reprocess omissions, not only detected spans.
   These proposed quality thresholds are unmet and require domain-owner review.
4. **Automated release:** NO-GO regardless of results in this gate. No automatic
   exporter or boolean/model-based release authorization is permitted. A future
   proposal would need independent representative tests, residual-risk acceptance,
   production security integration and a separately authorized policy change.

A finite zero-canary synthetic sample cannot establish universal privacy safety.
Recognition quality and identity/authorization safety are independently required.
