from ocr.classify import features
from ocr.fixtures.generate import raster
from ocr.ingest import encode
from ocr.quality import quality
from ocr.schemas import OCRPageResult, RasterPage


def test_blur_proxy_and_blank():
    def page(style):
        data = encode(raster("Public printed fixture text.", style=style))
        return RasterPage(0, data, 1400, 860, "id", features=features(data))

    p = page("blur")
    r = OCRPageResult(
        page_index=0,
        source="cpu_ocr",
        text="Public printed fixture text.",
        markdown="text",
        image_width=p.width,
        image_height=p.height,
    )
    assert "low_contrast_or_blur" in quality(r, p)
    assert "empty_nonblank" in quality(r.model_copy(update={"text": ""}), p)
    assert "low_contrast_or_blur" not in quality(r, page("clean"))
