# Phase 0 audit

Requested branch: `deploy-hf-space-augmentanonymous-14035769368160495102`.
Fetched HEAD: `fe293a173364dfbfc0166f9dd26b0ed1eaef8c57`.
Implementation worktree: `/workspace/augmentoolkit-ocr`, branch `implement/hybrid-ocr`.
The pre-existing `/workspace/augmentoolkit` checkout and its services are separate.
No existing tracked changes were present when the worktree was created.

The supplied specification is `spec (10).md`; `spec.md` is an exact canonical copy.
`Agent.md` describes the existing `Leon4gr45/augmentanonymous` Docker Space and
its unified port. It is not authorization to replace that deployment. The OCR
worker is packaged separately; no production upload or service mutation occurred.

Existing ingestion: `generation/core_components/chunking.py` uses PyMuPDF and
Tesseract when PDF text is short. Existing `augmentoolkit/utils/anonymization.py`
uses Presidio/NLTK and can return unchanged text on failure. Neither is replaced
or silently invoked by the new service. `ocr.handoff` supplies an explicit bridge
and classified evidence for a reviewed downstream anonymizer.

No existing OCR dispatcher, FreeLLMAPI endpoint, `alias-ocr` configuration,
provider enforcement protocol, or D2 router implementation was found in this
checkout. There were no FreeLLMAPI/OCR/HF credential variable bindings on this
machine, no outbound identities, and no configured VPN. This machine's hostname
is not Debian-Devil. Existing listeners at audit time were loopback 5173, 6379,
8000 and platform port 1040. They were not stopped or reconfigured.

The user deferred infrastructure access and live alias verification. Runtime
settings accept the eventual private FreeLLMAPI URL/key from environment secrets;
no address or credential was guessed. Actual serving model, downstream provider,
multimodal path, grounding, auth and quotas remain unverified live. GPU defaults
off. D1/D2 alias egress cannot be enabled by an environment flag.

RapidOCR 3.10.0 and ORT 1.24.1 were installed and exercised. RapidOCR's exact
`default_models.yaml` entries provide both PP-OCRv6 Small ONNX SHA256s, retained
in `ocr/models.json`; actual downloaded files matched. ONNX metadata includes the
recognition character dictionary. No classifier/alternate tier is downloaded.
The two models initialize on `CPUExecutionProvider`; GPU/CUDA is not installed.

ModelScope was initially denied by the environment proxy. Required ModelScope
hosts were added to the environment configuration draft while preserving existing
Hugging Face host additions. Subsequent HTTPS downloads succeeded with checksum
verification. This is artifact acquisition access, not document egress approval.
No model weights, real documents or secrets were committed.

The ModelScope public model metadata API declares Apache License 2.0 for
RapidAI/RapidOCR. Installed distribution metadata identifies RapidOCR Apache-2.0,
ORT MIT, pypdf BSD-3-Clause, Pillow MIT-CMU, and pypdfium2 BSD-3-Clause/Apache-2.0
with dependency licenses; retain bundled PDFium notices in redistribution.
