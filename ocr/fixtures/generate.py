"""CC0 synthetic corpus, deterministic, no real identities or personal data."""

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

TEXT_EN = "Public OCR benchmark.\nThe quick brown fox jumps over the lazy dog.\nInvoice total: 123.45 EUR."
TEXT_DE = "Öffentlicher OCR-Test.\nGrüße aus Köln: Äpfel, Öl und süße Birnen.\nRechnungsbetrag: 123,45 EUR."


def raster(text, dpi=200, style="clean"):
    scale = dpi / 100
    im = Image.new("RGB", (int(700 * scale), int(430 * scale)), "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype("DejaVuSans.ttf", int(23 * scale))
    d.multiline_text(
        (int(25 * scale), int(35 * scale)),
        text,
        font=font,
        fill=(150, 150, 150) if style == "contrast" else "black",
        spacing=int(16 * scale),
    )
    if style == "table":
        for y in (170, 220, 270):
            d.line((25 * scale, y * scale, 650 * scale, y * scale), fill="black", width=2)
        for x in (25, 300, 650):
            d.line((x * scale, 170 * scale, x * scale, 270 * scale), fill="black", width=2)
    if style == "blur":
        im = im.filter(ImageFilter.GaussianBlur(1.5 * scale))
    if style == "rotated":
        im = im.rotate(90, expand=True, fillcolor="white")
    return im


def born_digital(text=TEXT_EN):
    w = PdfWriter()
    p = w.add_blank_page(width=600, height=400)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    p[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})}
    )

    def escape(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = DecodedStreamObject()
    stream.set_data(
        (
            "BT /F1 18 Tf 30 340 Td 30 TL "
            + " T* ".join("(" + escape(line) + ") Tj" for line in text.splitlines())
            + " ET"
        ).encode("cp1252")
    )
    p[NameObject("/Contents")] = w._add_object(stream)
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def generate(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    manifest = []
    for lang, text in [("en", TEXT_EN), ("de", TEXT_DE)]:
        for dpi in (100, 200, 300):
            name = f"{lang}-{dpi}.png"
            raster(text, dpi).save(root / name)
            manifest.append(dict(file=name, text=text, category="printed", language=lang, dpi=dpi))
        name = f"{lang}-text.pdf"
        (root / name).write_bytes(born_digital(text))
        manifest.append(dict(file=name, text=text, category="embedded", language=lang))
    for style in ("contrast", "blur", "rotated", "table"):
        name = f"en-{style}.png"
        raster(TEXT_EN, style=style).save(root / name)
        manifest.append(dict(file=name, text=TEXT_EN, category=style, language="en"))
    col = "Left column: public text.\nOne two three.                  Right column: public text.\n                                       Four five six."
    raster(col).save(root / "columns.png")
    manifest.append(dict(file="columns.png", text=col, category="columns", language="en"))
    raster("Receipt\nApples 2.00 EUR\nBread 3.00 EUR\nTOTAL 5.00 EUR").save(root / "receipt.png")
    manifest.append(
        dict(
            file="receipt.png",
            text="Receipt\nApples 2.00 EUR\nBread 3.00 EUR\nTOTAL 5.00 EUR",
            category="receipt",
            language="en",
        )
    )
    Image.new("RGB", (700, 430), "white").save(root / "blank.png")
    manifest.append(dict(file="blank.png", text="", category="blank", language="en"))
    # Scanned PDF and mixed 20/100 page batches (no files with real PII).
    b = io.BytesIO()
    raster(TEXT_DE).save(b, format="PDF", resolution=200)
    scan = b.getvalue()
    (root / "de-scan.pdf").write_bytes(scan)
    manifest.append(dict(file="de-scan.pdf", text=TEXT_DE, category="scanned_pdf", language="de"))
    for count in (20, 100):
        w = PdfWriter()
        for i in range(count):
            w.append(PdfReader(io.BytesIO(born_digital(TEXT_EN) if i % 2 == 0 else scan)))
        with (root / f"mixed-{count}.pdf").open("wb") as f:
            w.write(f)
    (root / "damaged.pdf").write_bytes(b"%PDF-1.7\ncorrupted")
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("directory")
    generate(p.parse_args().directory)
