from collections import Counter


def quality(result, page, threshold=0.7):
    flags = []
    if not page.features.get("blank") and page.features.get("ink_contrast", 1000) < 145:
        flags.append("low_contrast_or_blur")
    if not result.text.strip() and not page.features.get("blank"):
        flags.append("empty_nonblank")
    if 0 < len(result.text.strip()) < 5 and page.features.get("line_density", 0) > 0.02:
        flags.append("suspiciously_short")
    scores = sorted(b.confidence for b in result.blocks if b.confidence is not None)
    if scores and scores[int((len(scores) - 1) * 0.1)] < threshold:
        flags.append("low_confidence")
    lines = [s.strip() for s in result.text.splitlines() if s.strip()]
    if len(lines) > 5 and max(Counter(lines).values()) > len(lines) * 0.6:
        flags.append("repeated_text")
    if result.text.count("```") % 2:
        flags.append("markdown_unbalanced")
    if "\ufffd" in result.text:
        flags.append("unresolved_noise")
    if page.features.get("possible_columns") and result.source == "cpu_ocr":
        flags.append("reading_order_unverified")
    return list(dict.fromkeys(result.flags + flags))
