# Decisions and deviations from spec.md

1. **Canonical spec**: preserve `spec (10).md`; copy it byte-for-byte to `spec.md`.
2. **Unavailable infrastructure**: user explicitly deferred Debian/alias access.
   Deliver runtime-configurable OpenAI-compatible integration and offline mocks;
   do not claim actual model, provider, grounding or D2 qualification.
3. **D2 fail closed**: there is no trustworthy executed-destination/fallback
   attestation in this checkout. No metadata/environment flag enables D2 alias or
   remote HF routing. The approved catalogue is recorded without changing its
   existing policies. Local CPU remains available for D2.
4. **Ingestion isolation**: use pypdf with decompression/work caps plus PDFium in a
   disposable limited parser process. No persistence, credential inheritance or
   arbitrary remote fetch. This is additional resource containment, not a sandbox
   claim. Linux is the supported dispatcher deployment target.
5. **CPU classification**: pin detector and recognizer only. Do not silently add a
   differently tiered orientation model. EXIF and PDF rotation are preserved;
   scanned text rotated inside the page is a known accuracy limitation.
6. **Provenance first**: output selection controls assembled representations;
   normalized source-page evidence and sensitivity remain available. Alias boxes,
   confidence, semantic tables/math and verified provider identity remain null or
   false unless empirically qualified. Quality alternatives retain disagreements.
7. **Deployment isolation**: new `ocr.worker_api:app` and `ocr.api:app` are separate
   from existing Augmentoolkit. The explicit client/anonymization handoff bridge
   does not silently replace Tesseract or the legacy anonymizer in production.
8. **Performance gates**: CPU measurements on this cloud machine are not HF CPU
   Basic measurements. OpenVINO, genuine handwriting, human-annotated box IoU,
   structural table fidelity and live hybrid cost/quota qualification remain
   outstanding. Overflow remains disabled. Synthetic corpus labels are known
   rendered source text; no independent human-reviewed document corpus is claimed.
