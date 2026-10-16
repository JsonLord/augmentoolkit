import io

import pytest
from ocr.config import Settings
from ocr.fixtures.generate import born_digital, generate
from ocr.ingest import decode_image, ingest, usable_text
from ocr.schemas import OCROptions, SafeError
from PIL import Image


def test_embedded_skip_and_force():
    p = ingest(born_digital(), "file.pdf", OCROptions(), Settings())
    assert len(p) == 1 and p[0].embedded and p[0].index == 0
    assert (
        ingest(born_digital(), "file.pdf", OCROptions(force_ocr=True), Settings())[0].embedded
        is None
    )


@pytest.mark.parametrize(
    "data,name", [(b"%PDF-broken", "a.pdf"), (b"hello", "a.png"), (b"hello", "a.txt")]
)
def test_malformed(data, name):
    with pytest.raises(SafeError):
        ingest(data, name, OCROptions(), Settings())


def test_signature_and_limits(png):
    with pytest.raises(SafeError, match="signature_mismatch"):
        ingest(png, "a.jpg", OCROptions(), Settings())
    with pytest.raises(SafeError, match="image_limit"):
        ingest(png, "a.png", OCROptions(), Settings(max_pixels=100))
    with pytest.raises(SafeError, match="upload_limit"):
        ingest(png, "a.png", OCROptions(), Settings(max_upload=10))
    assert not usable_text("a\ufffd\ufffd")


def test_twenty_page_mixed(tmp_path):
    generate(tmp_path)
    pages = ingest((tmp_path / "mixed-20.pdf").read_bytes(), "a.pdf", OCROptions(), Settings())
    assert len(pages) == 20 and [p.index for p in pages] == list(range(20))
    assert sum(p.embedded is not None for p in pages) == 10
    with pytest.raises(SafeError, match="page_limit"):
        ingest(
            (tmp_path / "mixed-20.pdf").read_bytes(),
            "a.pdf",
            OCROptions(max_pages=2),
            Settings(),
        )


def test_tiff_and_bomb(png):
    b = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(
        b,
        format="TIFF",
        save_all=True,
        append_images=[Image.new("RGB", (40, 40), "black")],
    )
    assert len(ingest(b.getvalue(), "pages.tiff", OCROptions(), Settings())) == 2
    with pytest.raises(SafeError):
        decode_image(png, 1)
