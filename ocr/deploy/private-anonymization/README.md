# Private staging specification — NOT deployed

The declarative `staging.json` and `.service.disabled` template are review artifacts.
`deployment_enabled=false`, production identity is BLOCKED, and both the real host
adapter and worker entrypoints are unset. This task does not supply a fake identity
provider or a default production worker. Do not enable the unit as-is or use the
isolated test adapter as production wiring. There are no new endpoints or ports.

## Qualified resource profile and remaining host gates

The tested baseline is ONE persistent model instance, two inference threads,
one active job, four vCPU and an 8-GiB memory ceiling. Tests used a bounded Docker
probe on this cloud machine, with an additional live 1.5-GiB memory/CPU load.
The first two runs peaked around 4.4 GiB total cgroup usage, leaving about 3.6 GiB.
See the exact per-run artifacts for startup, warm latency, process RSS and headroom.
The third probe also measures native TCP listeners. These are synthetic short-text
measurements, not qualification of maximal documents or the actual Debian host.

Propose at least 12 GiB host RAM to reserve room beyond this 8-GiB worker profile;
measure the real host's competing applications before staging. This is a deployment
baseline, not proof that 12 GiB is the universal minimum. The host application must
enforce the manifest's bounded queue, input, retention and timeout settings. No such
production host application was available to qualify here. Do not use multiple
model instances under this memory budget without a new load test.

## Provisioning sequence after separate authorization

1. Pin the reviewed code SHA and Python 3.11 runtime. Install the existing OCR and
   separate anonymization hash-locked dependencies in a dedicated venv. No NER
   dependency or checkpoint is added to the public HF worker image.
2. Provision the fixed model snapshot explicitly, verify `artifacts.json`, then make
   its cache read-only to the worker. Runtime sets HF/Transformers offline and
   disables telemetry. The supplied unit isolates networking and allows no public
   listeners; external transport is not implemented or authorized.
3. Allocate a dedicated non-login UID and encrypted persistent local filesystem.
   State/review/candidate/export directories must be owned by that UID, mode 0700;
   classified files and SQLite database are 0600, with UMask=0077. Original OCR
   evidence is immutable, private and separately retained. Model weights are public
   artifacts but immutable/read-only at runtime. Do not log classified paths/text.
4. Provision the ledger ONCE using `SQLiteApprovalLedger(path, initialize=True)`
   in the private 0700 state directory, then record `ledger.ledger_id` in private
   configuration. Normal startup must use `SQLiteApprovalLedger(path,
   expected_id=recorded_id)`; it MUST NOT use initialize=True. Startup refuses
   missing/corrupt/wrong-ID storage instead of resetting authorization history.
   Existing pre-Gate03c ledgers require an explicit audited migration or key rotation;
   automatic destructive migration is deliberately absent.
5. Integrate `HostSecurityAdapter` against the real identity/session verifier,
   immutable completed-review and candidate/export stores, current ACL/revocation
   data and operator destination registry. Authenticate opaque credentials, not
   requested subject IDs. Review and destination data must be rechecked at export.
   The concrete adapter and private host entrypoint remain BLOCKED here.
6. Load a signing key from private secret storage (the unit uses systemd credentials).
   All consumers in a single key domain must use the same durable ledger. This
   implementation supports single-host SQLite, or distinct actual signing keys per
   host. `validate_key_topology(..., signing_keys=...)` compares computed fingerprints;
   different aliases for the same secret do not bypass the guard. Shared-key
   multi-host deployment is BLOCKED: no shared durable backend has been implemented.
   A configuration flag claiming shared durability is not qualification evidence.
7. After identity, physical destination and fresh recognition gates are resolved,
   repeat the resource/network/permission tests on the actual private staging host
   and obtain separate deployment authorization. Never point D1/D2 at Agent-0 or
   an external model in anticipation of anonymization.

## Destination and transport boundary

`PrivateFileTransport` supports only operator-configured private local directories.
It accepts a destination ID, never a path/URL, opens the verified directory without
symlink following, pins its device/inode, writes mode-0600 bytes and atomically
publishes under the artifact SHA-256 filename. It has no HTTP client, redirects,
public fallback or remote destination mechanism. Changed roots/policies are denied.

A successful export does NOT call this transport. A separate operation must
authenticate again, check an authoritative exported-artifact digest, and grant
`anonymization:private-transfer` for the retained classification and destination.
An approved JSON export alone does not authorize this. D1/D2 remain D1/D2 and the
payload retains `transmission_authorized=false`; the transport receipt records only
the explicitly authorized local delivery. Remote transmission stays disabled.

## Failure recovery and rollback

- Stop intake and the private worker before rollback; do not restart any production
  service as part of this PR. Drain/cancel pending work without publishing candidates.
- Keep the current ledger, pinned ledger ID and signing-key domain intact across
  code rollback. NEVER restore an older ledger snapshot while its signing key is
  still valid: this could resurrect consumed approvals. If durable state is lost,
  corrupt or replaced, fail closed; rotate the key and invalidate outstanding
  approvals before explicit new provisioning. SQLite backups/migrations require an
  application-consistent procedure, not a copy of a live database file.
- Switch the private code/venv pointer to the previously reviewed SHA only if that
  version understands the current ledger schema and contracts. Otherwise keep the
  worker stopped and perform an audited migration. Do not silently downgrade to
  the pre-Gate03c auto-create ledger behavior or legacy anonymizer.
- Preserve original evidence and all unapproved candidates as classified. Recovery
  may remove only abandoned `.anonymization-*` / `.private-transfer-*` staging files
  through `recover_staging(private_directory)`. It obtains an exclusive directory
  lock; active writers hold a shared lock. Symlinks and ordinary evidence filenames
  are retained. Killed-writer recovery is tested with real process termination.
- Unlinking is not secure erasure on SSDs/copy-on-write/snapshots. Encryption,
  retention, snapshot access and secure destruction remain private-host operator
  duties. No classified-file cleanup is scheduled or activated automatically here.
- A code rollback does not enable release. `automated_release_eligible=false`,
  deployment disabled and manual review/authorization remain mandatory.

The newly frozen holdout still has unmasked religious-reference canaries. Therefore
private deployment and real sensitive-document processing remain NO-GO, independent
of the successful local security-boundary tests. See the Gate 03c report.
