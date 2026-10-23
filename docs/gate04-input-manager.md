# Gate 04 M1 — private Input Manager

Based on accepted PR #6 `d5fba6bb49aa8d430010aab741e587a15e0fe249`. This change is qualified locally and in CI, not deployed. Staging remains at the separately accepted P0 revision. It does not enable jobs, dataset persistence, providers or sensitive processing.

## Contracts

All business routes retain the existing router's WORKER_TOKEN dependency. Missing/invalid credentials return 401; an unconfigured token returns 503.

| Endpoint | Contract |
| --- | --- |
| POST `/inputs/upload/{relative_path:path}` | Multipart `files`, including empty root suffix; aggregate file bytes <=16 MiB, <=100 files. Request body is bounded before multipart spooling (16 MiB +64 KiB overhead). |
| GET `/inputs/structure/{relative_path:path}` | Existing recursive FileStructure list; root alias `/inputs/structure/` preserved. |
| GET `/inputs/download/{relative_path:path}` | Attachment snapshot: octet-stream file or directory ZIP, nosniff/no-store; archive input <=64 MiB. |
| DELETE `/inputs/{relative_path:path}` | Delete validated file/directory; root forbidden. |
| POST `/inputs/directory` | JSON `relative_path`; parent must already exist. |
| POST `/inputs/move` | Existing source_relative_path/destination_relative_path schema; move/rename within inputs only. |

Duplicate uploads/directory names/move destinations return 409 without replacement. Invalid paths/root operations return 400, missing paths 404, exceeded limits 413; malformed request schemas retain FastAPI 422. OS write failures become sanitized 500 responses. No raw exception/path/content is logged by new operations.

Example synthetic responses:

```json
{"message":"Upload complete","uploaded_files":["Synthetic ü space.txt"],"errors":[]}
{"path":"Synthetic ü space.txt","is_dir":false,"children":null}
{"message":"Item moved"}
{"message":"Item deleted"}
{"detail":"Destination already exists"}
```

## Storage and security

All file state is under `ATK_DATA_ROOT/inputs`; service-owned temporary snapshots are under `ATK_DATA_ROOT/file_staging`, never `/app/inputs`. Directories are 0700 and uploaded/staged files 0600. Absolute paths, traversal, sibling-prefix escapes, backslash/control characters and symlinks (including nested, dangling or internal links) are denied. Delete and archive validate complete subtrees without following links. Uploaded ZIPs remain opaque files and are not extracted or executed.

The implementation reuses confined storage/get_safe_path, FileStructure and validated ZIP helpers. Uploads stage complete content, fsync it, then publish with an atomic no-overwrite hard link. Failed requests clean staged files; ordinary batch publication failures roll back already-published names under the same operation lock. This is not a crash-atomic multi-file transaction. Downloads snapshot under the lock before returning FileResponse, avoiding reopening a caller-mutable input path.

The application still has one worker/concurrency 32. The in-process lock serializes these API operations, not external processes or multiple hosts. Storage must be owned by this service and unavailable for concurrent local manipulation by untrusted users; inputs/staging must share a filesystem for hard-link publication. Multi-host/multi-writer storage and crash recovery of orphan temporary files are not qualified. Storage is ephemeral; restart/persistence is not promised.

## Existing UI reused

InputManager, tree, dropzone, download flow and existing modals remain. The file picker now accepts ordinary files rather than forcing directories; existing dropped-folder ZIP handling remains. A small Rename modal uses the real move API. Unicode download names use RFC 5987 decoding. Root pseudo-nodes no longer cause an exception during folder creation beside a selected root file.

On lock/unlock, private listing caches are invalidated; responses from an earlier access epoch cannot repopulate them. This supports refresh → unlock → current listing without persisting operator credentials. No token is stored in browser storage.

## Verification

- 191 parent tests passed, including 38 new file-operation regressions and the expanded router auth matrix.
- Default OCR suite: 478 passed, 13 existing opt-in skips. No OCR code/model/prompt changed.
- Python compile/import and Ruff for new modules/parent tests passed.
- Frontend build and real Docker build passed.
- Standalone Playwright lifecycle passed: upload → immediate tree listing → exact byte/Unicode-name download → directory creation → rename using move → refresh/unlock → delete → verify absence after another refresh.
- Complete combined-image P0/browser/OCR smoke also passed with the M1 lifecycle appended: 10 reloads, 20 navigations, direct refresh routes, parallel listings/assets, one real synthetic D0 OCR request, nine classification rejections, auth and disabled queue checks; zero concurrency warnings.
- Frontend lint was run. The three changed files retain six pre-existing ESLint errors and existing hook/refresh warnings; comparison against the accepted baseline found no new findings. This is not a claim of a globally clean frontend lint run.

Commands: `pytest -c pytest-ocr.ini tests/parent/test_input_files.py` for the fast loop; frontend build/lint during editing; `pytest -c pytest-ocr.ini tests/parent`, `pytest -c pytest-ocr.ini`, compilation/import, Docker build and `python tests/parent/container_smoke.py --image augmentoolkit-m1 --evidence ...` at the release gate. UI-only debugging did not repeat OCR inference. No Hugging Face live test/deployment was performed for M1.

CI now retains parent/frontend/Docker/smoke logs and OCR logs as artifacts with shell pipefail, so tee cannot hide failures. Source-pinned exact-head CI/PR URLs are returned with delivery. Raw local container evidence and the frontend lint baseline comparison are in `docs/gate04-m1-evidence/`.

Decision: GO for review and an independently authorized synthetic staging deployment candidate; NO-GO for sensitive documents, real job execution, persistence/providers or automated release. Existing missing legacy functionality remains excluded. M2 follows as a separate reviewable PR after M1 is green. `automated_release_eligible=false`.
