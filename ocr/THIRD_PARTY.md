# Distribution notices

The application code inherits the repository's MIT license, copied to `ocr/LICENSE`
for a standalone worker staging directory. The Space README's license field refers
to that application code, not to every bundled model/dependency.

PP-OCRv6 Small detector/recognizer artifacts come from the RapidAI/RapidOCR
ModelScope repository, revision `v3.10.0`; its public metadata declares Apache
License 2.0. Exact HTTPS URLs and upstream SHA256s are in `models.json`. Preserve
Apache license/attribution requirements when distributing the baked model files.
The pinned RapidOCR distribution includes its Apache-2.0 full license in installed
distribution metadata. No additional model redistribution rights are asserted.

Runtime dependencies retain their installed `.dist-info` license files in the
Docker image. Key metadata: RapidOCR Apache-2.0; ONNX Runtime MIT; Pillow MIT-CMU;
pypdf BSD-3-Clause; pypdfium2 BSD-3-Clause/Apache-2.0 plus bundled PDFium dependency
licenses; FastAPI MIT; HTTPX BSD-3-Clause. See those full installed notices rather
than treating this summary as a substitute. Debian's DejaVu font copyright notice
is retained under `/usr/share/doc/fonts-dejavu-core/copyright`.

Review all transitive notices and operator/provider terms before publication.
Document ownership/processing rights and downstream provider compliance are the
operator's responsibility; the software makes no regulatory certification claim.
