"""Bounded parsing. No URLs, no caller-selected server paths, no persistence."""

import hashlib
import io
import math
import warnings
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import apply_configuration

from .classify import features
from .schemas import RasterPage, SafeError

EXT = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".tif": "TIFF", ".tiff": "TIFF"}


def usable_text(text):
    s = text.strip()
    return (
        len(s) >= 24
        and sum(c.isalpha() for c in s) >= 15
        and s.count("\ufffd") / len(s) < 0.01
        and sum(c.isprintable() or c.isspace() for c in s) / len(s) > 0.98
    )


def encode(im):
    b = io.BytesIO()
    im.convert("RGB").save(b, format="PNG")
    return b.getvalue()


def decode_image(data, max_pixels):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            im = Image.open(io.BytesIO(data))
            if im.format not in ("PNG", "JPEG", "TIFF") or im.width * im.height > max_pixels:
                raise SafeError("image_limit")
            im.load()
            return ImageOps.exif_transpose(im).convert("RGB")
    except SafeError:
        raise
    except (Exception, Image.DecompressionBombWarning):
        raise SafeError("invalid_image") from None


def ingest(data: bytes, filename: str, options, settings):
    with apply_configuration(
        maximum_declared_stream_length=8_000_000,
        zlib_maximum_output_length=8_000_000,
        array_based_stream_maximum_output_length=8_000_000,
        lzw_maximum_output_length=8_000_000,
        run_length_maximum_output_length=8_000_000,
        stream_decoding_work_maximum_length=32_000_000,
        page_tree_maximum_entries=1000,
        image_maximum_buffer_size=60_000_000,
    ):
        return _ingest(data, filename, options, settings)


def _ingest(data: bytes, filename: str, options, settings):
    if not data or len(data) > settings.max_upload:
        raise SafeError("upload_limit")
    ext = Path(filename).suffix.lower()
    digest = hashlib.sha256(data).hexdigest()
    pages = []
    total = 0
    limit = min(options.max_pages, settings.max_pages)

    def add(im, index, text=None, rotation=0):
        nonlocal total
        if im.width * im.height > settings.max_pixels:
            raise SafeError("image_limit")
        total += im.width * im.height
        if total > settings.total_pixels:
            raise SafeError("document_pixel_limit")
        image = encode(im)
        pages.append(
            RasterPage(
                index,
                image,
                im.width,
                im.height,
                f"{digest}:{index}",
                rotation,
                text,
                features(image),
            )
        )

    try:
        if ext == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise SafeError("signature_mismatch")
            import pypdfium2 as pdfium
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise SafeError("encrypted_pdf")
            if not 0 < len(reader.pages) <= limit:
                raise SafeError("page_limit")
            with pdfium.PdfDocument(data) as doc:
                for i, p in enumerate(reader.pages):
                    # Avoid unbounded text-stream decompression before extraction.
                    contents = p.get_contents()
                    if contents and len(contents.get_data()) > 8 * 1024 * 1024:
                        raise SafeError("pdf_stream_limit")
                    text = (
                        p.extract_text() or ""
                        if options.extract_pdf_text_layer and not options.force_ocr
                        else ""
                    )
                    text = text if usable_text(text) else None
                    page = doc[i]
                    try:
                        w, h = page.get_size()
                        scale = 200 / 72
                        if (
                            not math.isfinite(w * h)
                            or w <= 0
                            or h <= 0
                            or w * h * scale * scale > settings.max_pixels
                        ):
                            raise SafeError("image_limit")
                        # Embedded pages need no raster for OCR; keep source page dimensions.
                        if text:
                            add(
                                Image.new("RGB", (max(1, int(w)), max(1, int(h))), "white"),
                                i,
                                text,
                                int(p.get("/Rotate", 0)),
                            )
                        else:
                            bitmap = page.render(scale=scale)
                            try:
                                add(
                                    bitmap.to_pil(),
                                    i,
                                    rotation=int(p.get("/Rotate", 0)),
                                )
                            finally:
                                bitmap.close()
                    finally:
                        page.close()
        elif ext in EXT:
            with Image.open(io.BytesIO(data)) as source:
                if source.format != EXT[ext]:
                    raise SafeError("signature_mismatch")
                count = getattr(source, "n_frames", 1)
                if count > limit:
                    raise SafeError("page_limit")
                for i in range(count):
                    source.seek(i)
                    if source.width * source.height > settings.max_pixels:
                        raise SafeError("image_limit")
                    source.load()
                    add(ImageOps.exif_transpose(source).convert("RGB"), i)
        else:
            raise SafeError("unsupported_media")
    except SafeError:
        raise
    except Exception:
        raise SafeError("invalid_document") from None
    return pages
