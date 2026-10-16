import re

from .schemas import Block, SafeError


def polygon(points, width, height):
    p = [
        [
            round(min(1000, max(0, float(x) / width * 1000)), 3),
            round(min(1000, max(0, float(y) / height * 1000)), 3),
        ]
        for x, y in points
    ]
    return p, [
        min(x[0] for x in p),
        min(x[1] for x in p),
        max(x[0] for x in p),
        max(x[1] for x in p),
    ]


def grounding(text):
    blocks = []
    for match in re.finditer(r"!\[(text|table|figure|title|formula)\]\(([^)]*)\)", text):
        try:
            box = [float(v) for v in match[2].split(",")]
            if (
                len(box) != 4
                or not all(0 <= v <= 1000 for v in box)
                or box[0] > box[2]
                or box[1] > box[3]
            ):
                raise ValueError()
            blocks.append(Block(type=match[1], bbox_1000=box))
        except ValueError:
            raise SafeError("invalid_grounding") from None
    return blocks


def markdown_pages(pages):
    return "\n\n".join(
        f"<!-- page:{p.page_index + 1} -->\n\n{p.markdown}"
        for p in sorted(pages, key=lambda p: p.page_index)
    )
