# Native factual pipeline — compatibility preflight only

This increment follows the Dataset lifecycle in PR #10. It is **not a native generation qualification or runnable HF Job**.

The actual `generate_factual_qa_dataset` module had two Python syntax defects: a defaulted anonymize parameter preceded required cost parameters, and the chunk_text_list call had an extra comma. The patch repairs syntax without changing prompts, validators, sampling, Q&A semantics, anonymization guards or cost defaults. Its unused import-time nltk.download call is removed; runtime resource provisioning is not allowed.

An isolated worker dependency lock is in `requirements/native-pilot/`. It is independent of the frontend/control-plane package pins: their newer fsspec constraint selected an old datasets version incompatible with current PyArrow. The isolated resolution imports the native pipeline successfully, retains the tested HFHubManager SDK version (huggingface-hub 0.36.0), and installs neither Torch nor Transformers. No requirements or dependencies are added to the frontend Docker image.

Local reproduction, in an isolated environment:

```
uv pip sync --python /workspace/.native-pilot-venv/bin/python --require-hashes requirements/native-pilot/worker.lock
MPLCONFIGDIR=/workspace/native-matplotlib /workspace/.native-pilot-venv/bin/python tools/native_factual_preflight.py --evidence /workspace/native-factual-preflight.json
```

The real module import is guarded against network connections and NLTK downloads; the preflight does not replace any generation step or fabricate Q&A. Measured import: about 1.7 seconds / 252 MB peak RSS in this environment. Native module compilation and import pass. Existing trained English Punkt tables are still missing: they must be pinned, provisioned and hashed at worker build before actual execution. The preflight reports this and always returns ready_for_live_generation=false.

Not done: a bounded provider request/spend adapter, pinned worker image, HF Job launch, native pipeline invocation, schema-valid generated Q&A, real Dataset persistence trial, or frontend generation activation. No provider model was invoked, no HF Job was launched, and no Space configuration changed. Future native execution must reuse HF_SOURCE_REPO/HF_WORK_REPO/HF_OUTPUT_REPO conventions with immutable inputs/configuration and validated output commits. No arbitrary module selection or public fallback is permitted. Those are pending work, not claims made by this preflight.

REAL_GENERATION: NOT RUN / NO-GO. Automated release remains disabled. This is a small compatibility increment toward the genuine pipeline, separated from the qualified Phase C implementation.
