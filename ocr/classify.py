import io

import numpy as np
from PIL import Image, ImageFilter


def features(image: bytes) -> dict:
    with Image.open(io.BytesIO(image)) as im:
        gray = im.convert("L").resize((256, 256))
        a = np.asarray(gray, dtype=float)
        ink = a < 180
        contrast = float(a.std())
        edge = float(np.asarray(gray.filter(ImageFilter.FIND_EDGES), dtype=float).var())
        horizontal = int((ink.mean(axis=1) > 0.7).sum())
        vertical = int((ink.mean(axis=0) > 0.7).sum())
        # These are proxies, never semantic handwriting/table claims.
        density = float(ink.mean())
        ink_contrast = contrast / max(0.001, density**0.5)
        return dict(
            blank=density < 0.0001,
            contrast=contrast,
            ink_contrast=ink_contrast,
            edge_variance=edge,
            line_density=density,
            table_rulings=horizontal + vertical,
            possible_columns=bool(
                ink[:, :100].mean() > 0.03
                and ink[:, 156:].mean() > 0.03
                and ink[:, 120:136].mean() < 0.01
            ),
            complex=horizontal + vertical > 8 or (ink_contrast < 145 and density > 0.001),
            handwriting_proxy="unknown",
            orientation="landscape" if im.width > im.height else "portrait",
        )
