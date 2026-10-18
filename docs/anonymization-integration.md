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

The CLI reads a bounded OCRResult JSON file and exclusively creates the output
with mode 0600. It never overwrites an existing output, publishes content or creates
an approval. It prints only a constant status/code. No automatic NLTK/download
fallback exists. Model loading occurs explicitly at startup/command initialization,
not as a download during requests.

## Private application API

```python
from ocr.anonymization import AnonymizationEngine, ReleaseAuthority
from ocr.anonymization.recognizer import GLiNERRecognizer
from ocr.handoff import anonymization_handoff

# Startup on the private host; not on the public HF worker.
model = GLiNERRecognizer('/private/models/anonymization', device='cpu', threads=2)
engine = AnonymizationEngine(model)
# original_result is a completed, authorized OCRResult retained by the caller.
candidate = anonymization_handoff(original_result, engine)

# Separate operation AFTER a human reviews this exact candidate.
# Supply real application identity/ACL checks, not a constant-True function.
authority = ReleaseAuthority(private_settings.release_signing_key, app_authorizer)
approval = authority.approve(candidate, actor=authenticated_reviewer,
                             review_completed=True)
reviewed_json = authority.export(candidate, approval, format='json')
# Send/store only according to its RETAINED sensitivity and approved destination.
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
