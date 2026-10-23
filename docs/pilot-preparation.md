# D0 TXT preparation pilot

Stacked on PR #8, source aee4f752ab64f189fa3962aed320347323bfafed. No merge or live deployment. The generic Huey execution, recovery, remote providers and automated release remain disabled.

## Real user workflow

Unlock → Configs → select `pilot-preparation-config` → create a private YAML copy → Inputs → upload/select a public/synthetic UTF-8 TXT → select private config → explicitly confirm D0 → Prepare Dataset → Monitor → preview/download JSONL and manifest → reopen Outputs.

These are deterministic preparation chunks, **not Q&A training records**. No generation component is mocked or invoked. Chat, native pipeline Run and legacy output/monitor actions are visibly unavailable; the supported preparation actions remain usable. The home page contains the quick-start.

## API and enablement

All routes retain the parent WORKER_TOKEN dependency. `ATK_PREPARATION_ENABLED` defaults false; authenticated access returns 503 until an operator explicitly enables this pilot. Local synthetic tests alone set it true. This does not enable generic `/pipelines/run`.

| Route | Contract |
| --- | --- |
| POST `/pilot/preparation` | `source_path`, private `config_path`, explicit `sensitivity: D0`; 202 RUNNING or real rejection. Missing/D1/D2 classifications fail before input reading. |
| GET `/pilot/preparation` | Recoverable persisted job list. |
| GET `/pilot/preparation/{uuid}` | RUNNING / COMPLETED / FAILED and current phase; no raw source text or snapshot paths. |
| GET `/pilot/preparation/{uuid}/outputs` | Allowlisted artifact names only after completion. |
| GET `/pilot/preparation/{uuid}/preview` | First ten records; completion and integrity required. |
| GET `/pilot/preparation/{uuid}/download/{name}` | Verified attachment snapshot; only records.jsonl and manifest.json. |

401 missing/invalid operator credentials; 503 unconfigured auth or disabled pilot; 400 invalid classification/path/config/input; 413 size; 409 active-task or integrity conflict; 404 missing jobs/artifacts. Schema errors retain FastAPI 422. Backend errors contain no input text, keys or arbitrary exception messages.

A single local subprocess performs this allowlisted function. There is no second general scheduler, queue, Redis consumer, additional listener or arbitrary module/function request. One active task maximum; 15-second watchdog kills overrun tasks. Linux PR_SET_PDEATHSIG binds the child to its web parent. Restart marks RUNNING jobs FAILED and removes incomplete output/snapshots; completed artifacts remain accessible on the same filesystem. This is not cross-host persistence qualification.

Limits: 1 MiB UTF-8 TXT; 16 KiB private YAML, bounded YAML event count/depth, no aliases or Python constructors; 64..4096 codepoint chunks, overlap <256 and smaller than chunk size, <=4096 records and <=16 MiB JSONL. Only the `preparation` options are used; no native pipeline code is imported. The older chunking module initializes an optional tokenizer/parser stack; this pilot deliberately uses a small bounded codepoint splitter with no downloads. It does not claim token-size chunks.

Normalization is explicit: UTF-8 BOM removal, CR/CRLF → LF, NFC. Chunk offsets refer to normalized text. Source SHA-256 hashes the original bytes. Stable source/chunk IDs, source filename, exact private config SHA-256 revision and D0 classification appear in records. The manifest adds record count, output hash, normalization and runtime source revision.

## Storage and failures

Inputs/configs are snapshotted under private `ATK_DATA_ROOT/pilot_state/{uuid}` before launch; later UI edits cannot change a running job. These snapshots are excluded from the Outputs tree and removed at completion/failure/recovery. Public-facing completed artifacts live under `ATK_DATA_ROOT/outputs/preparation/{uuid}` (authenticated private storage). Files are 0600, directories 0700; confined-path/symlink rules remain in force. Artifacts and state use private fsynced temporary files/atomic replacement; completion is published last. Failed jobs cannot expose partial artifacts through the pilot download API. Download verifies artifact hashes. Corrupt state fails closed with an operator-recovery error.

The service assumes one ASGI process, service-owned local storage and no outside writers, as M1/M2 do. There is no claim of distributed locking, directory-fsync crash durability, bucket-mount POSIX compatibility or classified-data release approval. Ephemeral Space storage is still ephemeral.

## Evidence

- 272 parent tests passed, including 21 preparation cases plus expanded router authentication tests.
- OCR baseline: 478 passed, 13 existing opt-in skips; historical quality gates unchanged.
- Compile/import, scoped Ruff, npm ci/build and the combined Docker build passed. New PreparationPanel has no ESLint findings; the repository-wide pre-existing frontend lint debt is not claimed resolved.
- Real subprocess tests cover exact records/manifests, same-filesystem restart, D0 rejection, auth, disabled flag, bounds, source/config snapshots, timeout supervision, spawn failures, corruption and artifact tampering. Timeout uses a controlled process double; the normal subprocess run is real. Parent-death protection is implemented but a forced-parent-death integration experiment is not yet qualified.
- Local real browser flow produced three chunks from generated public TXT, previewed and downloaded exact bytes, then found the job on Outputs. No backend HTTP mocks, external model calls or real documents.
- Complete combined workload includes M1/M2 and preparation plus one synthetic CPU OCR inference and nine classification/auth/disabled-Huey checks. It passed. The first harness run emitted a response-callback teardown error despite passing assertions; the final harness detaches its response observer before closing the browser. Exact-head CI repeats the complete final image. Final local UI-only check mounts the freshly built frontend/final backend into the real combined image; no repeated OCR inference for UI edits.

Machine evidence is in `docs/pilot-evidence/`; CI logs remain artifacts. Exact final source/CI links are recorded in the PR.

## Phase A deployment preparation

PR #8's complete 693-file payload is separately prepared at `/workspace/pilot-phase-a/payload` with source/file hashes. Read-only HF API confirms private RUNNING `Leon4gr45/augment` at rollback revision `d26a7807f1a46c44edb51d81d8afec081c6a78a8`. Its 630-file prior HF tree is downloaded and hashed. The check-only upload guard passed target/privacy/revision, unchanged disabled execution/routing flags, offline mode, payload hashes and all four source-head checks.

Only after separate approval:

```
/workspace/.ocr-venv/bin/python /workspace/pilot-phase-a/upload_after_approval.py
```

This deploys PR8 only, not this preparation branch. Record the HF commit/runtime revision and run both synthetic live browser workflows. A separate rollback approval can execute `/workspace/.ocr-venv/bin/python /workspace/pilot-phase-a/rollback_after_approval.py`; it pins the deployed parent, restores the hashed prior tree and deletes newly introduced files. Neither command has been run. WORKER_TOKEN and visibility are unchanged. The full procedure is `/workspace/pilot-phase-a/PROCEDURE.md`.

## Independent qualifications

| Gate | Status |
| --- | --- |
| LOCAL_FUNCTIONAL, deterministic TXT | PASS locally; exact-head CI required before delivery |
| STAGING_FUNCTIONAL M1/M2/pilot | NOT RUN; upload/live enablement require explicit approval |
| DURABLE_STORAGE | NOT QUALIFIED; local restart alone does not prove HF persistence |
| REAL_GENERATION | NOT RUN; no provider calls or paid HF Jobs authorized |

Next: after a green preparation PR, add image/PDF CPU OCR to this allowlist, then qualify explicit private HF dataset snapshots/hydration rather than assuming bucket hard-link/rename semantics. Real persistence writes/restarts and native on-demand generation require their own approval. `automated_release_eligible=false`; production readiness is NO-GO.
