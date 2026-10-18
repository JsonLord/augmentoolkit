# Installation and trusted OCR integration

Run this only on approved private processing infrastructure. No D1/D2 image or
text should be sent to public Agent-0, FreeLLMAPI or another external model in
anticipation of anonymization. Existing dispatcher authorization stays in effect;
this feature neither adds a remote inference transport nor changes its policy.

## Reproducible setup

Python 3.11, Linux x86_64 CPU, at least 4 GiB free RAM plus headroom and about
3 GiB disk for the model/runtime are recommended. Provision the cache explicitly
with network access, then run offline. Keep the cache and classified input/output
files private. The repository pins model artifact SHA-256 hashes in
`ocr/anonymization/artifacts.json` and all runtime dependencies in a separate lock.
It does not add these dependencies to either existing OCR Dockerfile.

```bash
python3.11 -m venv .venv-anonymization
.venv-anonymization/bin/python -m pip install --require-hashes \
  -r ocr/requirements.lock -r ocr/anonymization/requirements.lock
# Explicit setup command: the only model download operation.
.venv-anonymization/bin/python -m ocr.anonymization.model \
  --cache /private/models/anonymization
```

The lock uses PyPI plus the official PyTorch CPU wheel index. The tested native
stack is GLiNER 0.2.29, torch 2.8.0+cpu, transformers 4.57.1, huggingface-hub
0.36.2, NumPy 2.4.6, safetensors 0.7.0 and Pydantic 2.14.1. See the hashed lock
for transitive versions. Offline loading verifies every required artifact against
both the cache manifest and repository pins. Missing files, inconsistent manifests,
unsupported runtime/configuration or inference errors fail closed. Setup may resume
a partial download of the same pinned files. A cache with a completed invalid
manifest is rejected; use a new explicitly chosen directory to reinstall.

```bash
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
.venv-anonymization/bin/python -m ocr.anonymization \
  --model-cache /private/models/anonymization \
  --input /private/evidence/ocr-result.json \
  --output /private/review/candidate.json
```

The CLI reads a bounded OCRResult JSON file, stages a complete output with mode
0600 in the target directory, checks full write/flush/fsync, then atomically hard-links
it into place without replacing any existing file or symlink. The filesystem must
support atomic hard links. Disk/write failures leave no partial target. Staging is
removed on ordinary errors; a process kill or cleanup failure can leave a hidden
0600 staging file, which stays classified and is never an approved export. The
content is fsynced before publication; directory-entry persistence after power loss
is not promised. The CLI never overwrites a target, publishes content or creates an approval. It prints only a constant status/code. No automatic NLTK/download
fallback exists. Model loading occurs explicitly at startup/command initialization,
not as a download during requests.

## Private application API

```python
from ocr.anonymization import AnonymizationEngine, ReleaseAuthority
from ocr.anonymization.recognizer import GLiNERRecognizer
from ocr.anonymization.schemas import Destination
from ocr.anonymization.approval_ledger import SQLiteApprovalLedger
from ocr.handoff import anonymization_handoff

# Startup on the private host; not on the public HF worker.
model = GLiNERRecognizer('/private/models/anonymization', device='cpu', threads=2)
engine = AnonymizationEngine(model)
# original_result is a completed, authorized OCRResult retained by the caller.
candidate = anonymization_handoff(original_result, engine)

# Separate operation AFTER a human reviews this exact candidate for this sink.
# These are real application integrations, deliberately NOT implemented as
# allow-all callbacks. See the architecture doc for their mandatory contracts.
# Provision ONCE during separately authorized setup; pin its returned ledger_id.
ledger = SQLiteApprovalLedger('/private/state/release-approvals.sqlite',
                              expected_id=private_settings.ledger_id)
destination = Destination(destination_id='reviewed-private-archive',
                          destination_class='private',
                          allowed_sensitivities=('D0', 'D1', 'D2'))
authority = ReleaseAuthority(
    private_settings.release_signing_key,
    app_release_acl,
    verify_review=app_authenticated_completed_review,
    consume_approval=ledger.consume,
    destinations=(destination,),
)
approval = authority.approve(
    candidate, actor=authenticated_reviewer_session,
    review_id=completed_review_record_id,
    destination='reviewed-private-archive',
)
reviewed_json = authority.export(
    candidate, approval, actor=authenticated_reviewer_session,
    destination='reviewed-private-archive', format='json',
)
# A second export with that approval is rejected. JSON retains classification
# and destination and says transmission_authorized=false. No transfer occurs.
```

The candidate has per-page `anonymized_text` and typed `tags`, without entity raw
strings. Consumers must handle it as a separate type from OCRResult. Full OCR
Markdown, blocks and alternatives remain only in classified evidence. Candidate
text can include missed identifiers and cannot bypass the review/approval boundary.
There is no HTTP endpoint for uploading to this feature or issuing release approvals.

The historical `anonymization_handoff(result, callable)` dictionary contract remains
for characterization/unrelated compatibility. It is **not** a ReleaseCandidate and
cannot be approved/exported through ReleaseAuthority. Never use it for a release.
The historical `augmentoolkit.utils.anonymization.anonymize_text()` remains available
only for compatibility and unchanged vulnerability characterization.

`generation/core_components/chunking.py` no longer calls that legacy function:
`anonymize=True` raises `legacy_anonymization_requires_reviewed_workflow` before
chunking or downstream generation. Migrate those callers to the typed OCR candidate
and separate review workflow. Ordinary `anonymize=False` behavior is unchanged.
There is deliberately no automatic release of untyped anonymized strings into
legacy generation pipelines, which cannot preserve the required classification.

## Verification and evaluation

```bash
ruff check ocr
ruff format --check ocr
python -m pytest -c pytest-ocr.ini
# Opt-in actual-model smoke + strict canary gate; a leakage failure must stay red.
RUN_ANONYMIZATION_MODEL=1 ANONYMIZATION_MODEL_CACHE=/private/models/anonymization \
  .venv-anonymization/bin/python -m pytest -c pytest-ocr.ini \
  ocr/tests/test_anonymization_integration.py -q
# Aggregate report, no source payloads; exit success means evaluation completed,
# not that qualification passed. Inspect recognition_qualification_passed.
.venv-anonymization/bin/python -m ocr.anonymization.evaluate \
  --cache /private/models/anonymization --output /private/review/evaluation.json
```

Install `ocr/requirements-dev.txt` for tests/lint. The default suite has no NER/model
download dependency. Real-model tests are explicit opt-in and separate from normal
CI. The historical optional legacy NLTK evaluation remains unchanged and is not
proof of the new engine's security. No benchmark request hits an external provider.

Neither production services nor Agent-0 were deployed/restarted for this feature.
Do not deploy it to Agent-0; it is a trusted application/dispatcher library and CLI.

## Runtime downloads and approval state

Legacy chunking no longer calls `nltk.download()` or initializes a remote tokenizer
at import time. It prefers pre-provisioned NLTK sentence data and locally cached
tokenizers. If sentence data is missing, untrained Punkt (or a simple sentence
splitter when NLTK itself is unavailable) keeps ordinary chunking operational.
Without a cached model tokenizer it uses a reversible UTF-8 byte representation
with two boundary tokens, conservatively overestimating typical BPE counts. This
can produce smaller chunks; it is not an exact model-specific token count. Setup
must provision the desired tokenizer/corpus explicitly for exact old behavior.
`anonymize=True` still blocks and never invokes the historical anonymizer.

The review verifier must authenticate the current session and retrieve completed
review evidence independently of the request. It returns the exact `VerifiedReview`
contract documented in the architecture file. The ACL must check the verified
subject and destination at issuance AND export. A boolean completed flag, arbitrary
actor string, or signed approval alone is insufficient. Destination IDs and classes
come from immutable private operator configuration, not upload/request fields.
The host must separately enforce the actual sink and its D1/D2 approval; this
library never grants arbitrary external transmission.

Replay consumption must be atomic and durable across all application instances
sharing a signing key. Put the SQLite ledger in an application-controlled private
persistent directory (database mode 0600). Do not delete or recreate it while
approvals remain valid. Rotate the signing key if replay state is lost. A successful
export consumes the approval; a delivery failure cannot reuse it. Obtain a new
review-bound authorization rather than retrying the consumed approval. No approval
or automated export is enabled by the candidate CLI or service startup.

The current actual-model follow-up results and decisions supersede the initial
failed canary run: see [PR #3 hardening](anonymization-pr3-hardening.md).


## Gate 03c supersedes staging readiness

The [Gate 03c report](anonymization-gate03c.md) records a fresh frozen 120-case
holdout with two unmasked religion canaries. Real identity/review integration is
BLOCKED. Private deployment, sensitive-document processing and automatic release
remain NO-GO. The [disabled staging specification](../ocr/deploy/private-anonymization/README.md)
defines explicit ledger provisioning, key domains, resource limits and rollback.
Normal ledger startup never creates missing storage; pass the pinned ledger ID.

## Gate 03d current recognition status

The default engine adds bounded literal religious/status term protection. To set
a trusted operator terminology policy in Python, pass
`AnonymizationEngine(model, religion_terms=("Shinto", "religionslos"))`; this replaces
the default list and must be reviewed/recorded by the host. Do not expose this
configuration as a request parameter. No remote model path or review bypass exists.

Human review must show both immutable original evidence and the candidate in the
authorized private application. That application/identity integration is still
BLOCKED. The [Gate03d first independent result](anonymization-gate03d.md) fails;
private deployment, sensitive-document processing and automatic release stay NO-GO.
Use the recorded first-result command and opt-in test environment from that report
for reproduction. Old 03c control tests intentionally still fail and selected-hybrid
development tests are separate; never replace qualification with a smoke test.
