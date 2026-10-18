"""One reversible source-coordinate view for hyphenated OCR line wraps."""

import re

LINE_WRAP = re.compile(r"(?<=[^\W\d_])-\r?\n(?=[^\W\d_])", re.UNICODE)


def dehyphenate(text):
    removed = {i for match in LINE_WRAP.finditer(text) for i in range(match.start(), match.end())}
    positions = tuple(i for i in range(len(text)) if i not in removed)
    return "".join(text[i] for i in positions), positions
