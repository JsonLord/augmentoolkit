# Phase C — three private Hugging Face Dataset repositories

Stacked on PR #9, `40838772461e4b6d9c908a19ed956211d36a213e`. No Buckets, mounts, volumes, new storage service, repository creation, deployment or paid inference. `/data` remains temporary working storage.

## Existing code reused

`HFHubManager` gains strict Dataset head/read/grouped-commit entry points. These delegate to `augmentoolkit/hf_persistence/dataset_commit.py`; existing legacy helpers stay unchanged. The lifecycle uses the existing `JobManifest` schema inside a versioned preparation envelope and reuses the existing preparation subprocess, atomic state writer, confined paths and private YAML/file APIs. The legacy `CheckpointManager` upload helpers are not used because they do not supply this workflow's parent-commit and revision/hash verification contract.

The interrupted standalone snapshot prototype/tests were preserved, with hashes, in `/workspace/pilot-phase-c-interrupted/`. There was no pre-existing uncommitted diff in `pilot_prepare.py`. The retained test ideas (exact bytes, conflict rejection, fresh-interpreter recovery) now exercise the required three-repository lifecycle.

## Dataset roles and commits

Actual configured repository IDs are mandatory. No default names and no assumption that repositories exist.

| Environment | Private Dataset content |
| --- | --- |
| HF_SOURCE_REPO | `inputs/{job_uuid}/{source_sha256}.txt` |
| HF_WORK_REPO | `configs/{job_uuid}/{config_sha256}.yaml`, `jobs/{job_uuid}/preparation.json`, immutable `jobs/{job_uuid}/completed/{manifest_sha256}.json` |
| HF_OUTPUT_REPO | `outputs/{job_uuid}/{artifact_sha256}/records.jsonl` and `manifest.json` |

The normal job uses four bounded commits: source; config + initial work manifest; JSONL + output manifest; final work manifest + immutable completion record. No per-record commits. All reads/writes specify `repo_type="dataset"`. Repositories must exist, be private, belong to the configured roles, and be reached through the approved HF Hub endpoint with an explicit HF_TOKEN binding.

Each write supplies `parent_commit`. A changed head is rejected; the only automatic reconciliation is an already-present exact batch whose bytes verify at that head (lost acknowledgement). Existing immutable paths with different bytes are rejected. Retries retain the same UUID, paths, hashes and expected parents. There is no silent rebase, destination substitution or default public fallback.

This is **not an atomic transaction across three repositories**. The final durable work manifest binds source/config/output repository IDs, pinned revisions, object paths and hashes. The returned final work commit SHA is recorded in the local receipt/evidence; a blob cannot contain its own introducing commit SHA. Discovery captures a work-repository head once and reads the manifest at that pinned revision; every asset then uses its recorded revision and independent SHA-256. Unreferenced objects from interrupted transactions are not treated as completed output and are not automatically deleted.

## Execution and restart behavior

Persistence is off by default. With approval/configuration enabled, the API snapshots the D0 source/config, persists and verifies them, creates the initial durable manifest, then launches the same bounded deterministic child. Local computation ends in PERSISTENCE_PENDING. Output JSONL is checked against the existing deterministic record builder and its declared schema/provenance before upload. COMPLETED / durability VERIFIED is published locally only after output readback and final durable-manifest verification.

Hub/network/credential/privacy/conflict failures produce PERSISTENCE_FAILED with a sanitized code. Partial local results cannot be downloaded through the pilot artifact API. An authenticated explicit POST `/pilot/preparation/{uuid}/retry-persistence` retries the failed stage; it does not regenerate already computed records. The UI shows durability, persistence errors and the retry action. Failed-stage snapshots remain private for retry; successful jobs remove processing snapshots.

Restart marks unfinished processing interrupted; it never auto-runs a subprocess or fabricates completion. Retained failed persistence can be retried explicitly. With a fresh local root, completed jobs are discovered from the work repository and restored through pinned verified references. Inputs/configs use generated UUID filenames to avoid trusting remote filesystem paths; original filename/provenance remain in job metadata. YAML content and ETag remain identical. An incomplete remote job recovers as FAILED / INTERRUPTED and requires an explicit new processing decision; it is not silently resumed. Recovery is read-only, so the previous remote RUNNING marker is not silently rewritten without a new write action.

Recovery verifies every asset and cross-repository binding before publishing local files; it uses private 0600 staging/no-overwrite hard links and publishes state last. Conflict or write failure rolls back newly created files. This is not a cross-filesystem or multi-host local transaction. Existing LOCAL_ONLY jobs are not silently promoted to durable jobs or automatically uploaded.

## Runtime contract and first real qualification

`deploy/pilot-datasets.env.example` is a proposed profile, not applied configuration. Dataset traffic needs HF_HUB_OFFLINE=0; the existing Docker/Space offline setting remains unchanged. The proposed isolated profile keeps generic Huey execution/recovery, GPU/remote OCR routes and external model inference disabled, with TRANSFORMERS_OFFLINE=1. HF_HUB_DISABLE_XET=1 and the explicit private download cache avoid unqualified home-directory cache writes. Storage traffic being enabled does not enable a model backend. Automated release remains ineligible.

ATK_DATASET_PERSISTENCE_ENABLED and ATK_DATASET_PERSISTENCE_WRITE_APPROVED default false. The latter is an operator configuration guard, **not a substitute for human authorization**. HF_TOKEN belongs in a secure credential binding, never YAML, source, browser state or command arguments. WORKER_TOKEN is unchanged.

Read-only preflight (requires actual IDs and a permitted Hub storage context):

```
python tools/qualify_dataset_persistence.py
```

After separate approval, from the clean reviewed checkout, one command runs the tiny synthetic D0 fixture, real preparation, four grouped commits, readback, and fresh-process recovery:

```
HF_HUB_OFFLINE=0 HF_HUB_DISABLE_XET=1 ATK_DATASET_PERSISTENCE_WRITE_APPROVED=true \
python tools/qualify_dataset_persistence.py --execute-writes --confirm-synthetic-d0 \
  --source-sha REVIEWED_COMMIT_SHA --root /workspace/dataset-trial \
  --evidence /workspace/dataset-trial-evidence.json
```

The final source SHA is provided with the PR delivery. HF_SOURCE_REPO, HF_WORK_REPO, HF_OUTPUT_REPO and HF_TOKEN must already be configured. On failure the private root/evidence are retained; use the same command with `--resume JOB_UUID` for failed persistence. A revision conflict requires operator review, not an implicit new parent. The script changes no Space settings, launches no HF Job, creates no repository, and calls no model. The fresh process verifies source bytes, YAML/ETag and exact JSONL bytes from pinned revisions. It is not a Space restart test.

## Qualification and blockers

Local verification: 306 parent tests passed (28 Dataset lifecycle and four repository-ID sync cases), scoped Ruff/compile/import and frontend build passed; the complete combined-image smoke passed with zero server tracebacks/concurrency warnings. OCR baseline: 478 passed and 13 existing opt-in skips; no OCR source/model changes. Final exact-head CI repeats the normal full Docker build.

CI uses only a disk-backed fake Hub transport; the normal preparation child and the recovery interpreter are real. Tests cover all three repository roles, private/missing/unauthorized repositories, explicit credentials, parent-CAS (including server 409), lost acknowledgements, immutable IDs, output/final-manifest failure, no premature completion, source/config/content hashes and ETags, fresh-process download, missing/corrupted artifacts, interrupted recovery, unauthorized destination rejection, D0 checks before Hub construction, and recovery conflict/disk-error cleanup.

The current environment has **no configured HF_SOURCE_REPO/HF_WORK_REPO/HF_OUTPUT_REPO**. Live preflight therefore fails closed before a Hub operation. No real Dataset writes have occurred. Machine evidence and exact test scope are in `docs/pilot-phase-c-evidence/`; final CI links are recorded in the PR.

- LOCAL_FUNCTIONAL: qualified with mocked Hub operations and real local processing/recovery; this is not proof of the HF backend.
- DURABLE_STORAGE: NOT QUALIFIED until the separately approved real trial passes.
- STAGING_FUNCTIONAL: NOT RUN for this branch; no settings/deployment changed.
- REAL_GENERATION: NOT RUN. Generic jobs, external inference and automated release remain disabled.

Minimum next approval: provide the three actual existing private Dataset IDs through configuration and authorize the single tiny synthetic trial using an appropriately scoped HF_TOKEN. No Space deployment/restart or paid inference is needed for that trial. Production readiness remains NO-GO; `automated_release_eligible=false`.


Repository-ID synchronization is a separate, narrowly authorized configuration action: `python tools/sync_dataset_repository_ids.py --apply --evidence /workspace/dataset-repository-sync.json`. It first verifies all three configured IDs/private Dataset repositories and the private Space's expected source revision, then changes only those three variable keys. It does not change secrets, offline/execution flags, visibility or source code, and never calls restart_space. HF may refresh its runtime as an inherent effect of a variable update. Partial failures record attempted/confirmed keys and stop without automatic rollback. Until all IDs are configured, it records WAITING_FOR_REPOSITORY_IDS without a Hub call. Synchronizing IDs does not approve Dataset writes or deploy Phase C.

The browser workload observer records statuses/headers without reading bodies inside reentrant callbacks (navigation may cancel a fetch). Completed HTTP probes verify the exact JSON error contract for every observed locked/disabled path. No authentication/classification assertion is skipped or weakened; the final CI image runs the full workload.
