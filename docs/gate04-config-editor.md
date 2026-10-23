# Gate 04 M2 — private configuration editor

Stacked on M1 PR #7 (`4e19ec78d0240119f850ea5df25f82b7c3f52319`), ultimately on accepted P0 PR #6. This is a development qualification candidate, not a deployed change. PRs #5/#6/#7 remain unmerged; the Space is unchanged by this milestone.

## Existing UI and contracts

The existing ConfigSelector now lists exact authorized bundled YAML aliases separately from private files. ConfigEditor opens a bundled template read-only, creates a named private copy through the existing copy contract, edits it in Monaco, saves, refreshes and reopens it. Monaco and its editor worker are bundled locally; the workflow does not fetch editor code from a CDN. Operator credentials remain in memory. Lock/refresh requires unlocking again.

All business endpoints use the existing parent router authentication: missing/invalid token 401, absent configured WORKER_TOKEN 503. No OCR/auth/execution flags changed.

| Endpoint | Behavior |
| --- | --- |
| GET `/configs/structure/` and suffix | Existing FileStructure tree, now using validated private-tree traversal. |
| GET `/configs/aliases` | Existing exact operator-authorized aliases; unchanged rules. |
| GET `/configs/content/{relative_path:path}` | UTF-8 plain text; ETag, X-Config-Read-Only, X-Config-Origin, no-store/nosniff. Exact bundled aliases resolve only through existing authorized source rules; other names resolve under private storage. |
| POST `/configs/content/{relative_path:path}` | UTF-8 YAML mapping, <=1 MiB request body. New filename creates without overwrite; existing file requires an exact If-Match ETag. Stale/missing version or deleted-version conflict is 409. Bundled writes 403. |
| POST `/configs/duplicate` | Existing source_alias/destination_relative_path JSON. Exact bundled alias required; atomically create private YAML copy, 409 on duplicate/alias collision. |
| POST `/configs/directory` | Existing relative_path schema; private directories only. |
| POST `/configs/move` | Existing source_relative_path/destination_relative_path schema; private files/directories only, no overwrite. File destinations must remain YAML; bundled alias collisions denied. |
| DELETE `/configs/{relative_path:path}` | Validated private subtree deletion; root denied. |

Read missing configurations return 404. Invalid storage paths, invalid UTF-8, non-mapping YAML, unsafe YAML constructors and syntax errors return 400; oversized requests 413. Schema errors retain FastAPI 422. Write failures return sanitized 500 without exposing exception details. YAML errors report line/column, not source contents. Source viewing is not validation: an invalid bundled template can be viewed, but cannot be copied/saved as executable YAML.

Synthetic examples (ETag is the quoted SHA-256 of exact bytes):

```json
{"message":"Private configuration copy created","path":"Private ü configuration.yaml","etag":"\"<sha256>\""}
{"message":"Configuration saved","etag":"\"<sha256>\""}
{"detail":"Configuration changed or missing version; reload before saving"}
{"detail":"Invalid YAML at line 1, column 9"}
```

## Storage and trust boundaries

Bundled source remains read-only. Private YAML is under `ATK_DATA_ROOT/external_configs`, with 0600 files/0700 directories and service-owned staging under `ATK_DATA_ROOT/file_staging`. Traversal, sibling escapes and symlinks are denied through the existing confined-path helpers. Validation uses PyYAML safe_load; no pipeline functions or YAML-named Python modules are imported/executed. Syntax validation does not promise semantic validity for every legacy pipeline.

Saves fsync the complete temporary file before an atomic rename of an explicitly version-matched existing file, or atomic no-overwrite hard-link creation. A failed fsync/rename preserves the old file and cleans staging. API operations share M1's in-process lock. This requires one process, service-owned storage with no outside writers, and staging/configs on one filesystem. It is not distributed compare-and-swap, a durable multi-file transaction, or an fsync-backed directory crash-recovery guarantee. Storage remains ephemeral; HF persistence is unqualified.

Encoded nested configuration IDs have an explicit frontend refresh rule; backend routes and their authorization still take precedence. In-flight loads are discarded after navigation, old editor content is cleared during loading, and an old save response cannot populate a newly selected configuration's editor state. Folder creation beside a selected root file guards Arborist's virtual root, matching the M1 fix.

## Qualification scope

- Parent suite: 239 passed, including 32 new private-config tests, four encoded-route regressions, and the expanded route authorization matrix.
- Default OCR suite: 478 passed, 13 existing opt-in skips. Historical model quality gates are unchanged, not claimed to pass.
- Python compile/import and scoped Ruff passed; npm ci/frontend build and combined Docker build passed.
- Real local Playwright acceptance: select generated read-only YAML alias → read → private Unicode/space-named copy → edit → save → refresh/unlock → reopen through selector → exact-byte comparison. No frontend/backend requests mocked. Same-origin editor loading checked.
- Complete local combined-image smoke preserved P0 and M1: authentication, nine classification denials, one real synthetic D0 CPU OCR inference, disabled queue/submission 503, deep-link refresh/concurrent browser workload, full Input Manager lifecycle, and M2 editing lifecycle. One server process, zero concurrency warnings/tracebacks; peak sampled RSS 802316288 bytes. This local smoke preceded the final UI-only stale-state guard; the final editor/selector guards were separately checked with the UI-only workflow using the freshly built frontend mounted read-only into the combined image, and exact-head CI runs the complete final image again.
- Lint comparison of changed frontend files: seven existing errors versus eight on the M1 base, no new findings. This is not a globally clean frontend lint result.

Commands: `python -m py_compile api.py tasks.py private_configs.py`; `python -c "from api import app; print('API_IMPORT_OK')"`; scoped Ruff as in application CI; `python -m pytest -c pytest-ocr.ini tests/parent`; `python -m pytest -c pytest-ocr.ini`; `npm --prefix atk-interface ci`; `npm --prefix atk-interface run build`; Docker build; `python tests/parent/container_smoke.py --image augmentoolkit-m2 --evidence ...`. CI uses the same full smoke against its source-pinned image and retains complete test/build/browser logs as artifacts. Development environment downloads of Actions logs/artifacts remain blocked by its outbound policy; check status and sanitized failure annotations remain accessible.

Evidence: `docs/gate04-m2-evidence/` contains sanitized local container/UI and lint comparison JSON. Local image tags/build markers are not deployment revision claims. CI exact-head URLs and source SHA accompany the PR delivery.

## Decisions and limitations

GO for formal development review and consideration as a synthetic private staging candidate after a separately authorized deployment. NO-GO for production/sensitive processing, persistence, real jobs/consumers, external providers and automatic release. No HF deployment/live qualification, dataset writes, real documents, job execution or provider calls were performed. Existing Chat/LLM and other legacy workflows remain excluded. Gate 04 remains incomplete until separately authorized staging and broader required qualifications pass. `automated_release_eligible=false`.
