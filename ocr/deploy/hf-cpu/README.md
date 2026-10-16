---
title: Authenticated CPU OCR
emoji: 📄
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
license: mit
---

Separate stateless PP-OCRv6 Small CPU worker. This is not the existing Augmentoolkit
Space. No public demo or document persistence. Set **OCR_CPU_AUTH_TOKEN** as a Space
secret. `/health` is public and content-free; all processing, capabilities and
readiness endpoints require `Authorization: Bearer ...`.

Create a separate CPU Basic Space only after owner approval. From repository root,
copy `ocr/` and this Dockerfile into a staging directory, with this README at its
root. Build context must contain `ocr/requirements.lock`, code, and `ocr/models.json`.
Do not upload the entire Augmentoolkit repository, inputs, credentials or outputs.
CPU Basic sizing (2 vCPU/16 GB) must be confirmed on the selected account.

One process warms detector and recognizer. Readiness is 503 if models fail integrity
or initialization. Weights are downloaded and checksum-verified at build time;
runtime inference does not need model-download egress. Space sleep destroys
processes; readiness must be rechecked after wake. Token privacy is not D2 processing
authorization. Default dispatcher policy allows remote CPU only for public D0.

See `ocr/docs/deployment.md` for build, validation, approval and rollback steps.
