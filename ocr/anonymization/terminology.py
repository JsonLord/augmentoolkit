"""Bounded literal protection of religious terminology, not personal attribution.

A match says only that the source contains a protected religious/status term.
Negations, quotations and general topics are intentionally protected too. No
relationship between a term and a nearby person is inferred. This finite lexicon
is neither a spelling corrector nor an exhaustive religion/affiliation detector.
"""

import unicodedata

from ocr.schemas import SafeError

from .normalization import dehyphenate
from .schemas import Entity

TERMINOLOGY_REVISION = "religious-terminology-v1"
# Explicit EN/DE words and inflections, never fixture IDs, names or context templates.
# Ambiguous adjectives (e.g. orthodox, evangelical) remain contextual model decisions.
DEFAULT_TERMS = (
    "religionslos",
    "konfessionslos",
    "konfessionsfrei",
    "ohne Konfession",
    "nicht religiös",
    "no religious affiliation",
    "no religion",
    "nonreligious",
    "non-religious",
    "atheist",
    "atheists",
    "atheism",
    "atheistisch",
    "Atheismus",
    "agnostic",
    "agnostics",
    "agnosticism",
    "agnostisch",
    "Agnostizismus",
    "Christentum",
    "Christianity",
    "Christen",
    "Christian",
    "Christians",
    "christlich",
    "christliche",
    "christlichen",
    "Katholizismus",
    "Catholicism",
    "katholisch",
    "katholische",
    "katholischen",
    "Catholic",
    "Catholics",
    "Protestantismus",
    "Protestantism",
    "Protestant",
    "Protestants",
    "evangelisch",
    "evangelische",
    "evangelischen",
    "Judentum",
    "Judaism",
    "jüdisch",
    "jüdische",
    "jüdischen",
    "Jewish",
    "Jude",
    "Jüdin",
    "Juden",
    "Jews",
    "Islam",
    "Muslim",
    "Muslims",
    "muslimisch",
    "muslimische",
    "muslimischen",
    "islamisch",
    "islamische",
    "islamischen",
    "Buddhismus",
    "Buddhism",
    "Buddhist",
    "Buddhists",
    "buddhistisch",
    "buddhistische",
    "buddhistischen",
    "Hinduismus",
    "Hinduism",
    "Hindu",
    "Hindus",
    "hinduistisch",
    "hinduistische",
    "hinduistischen",
    "Sikhismus",
    "Sikhism",
    "Sikh",
    "Sikhs",
    "sikhistisch",
    "Jainismus",
    "Jainism",
    "Jain",
    "Jains",
    "jainistisch",
    "Bahai",
    "Baháʼí Faith",
    "Bahá'í",
    "Baha'i",
    "Shinto",
    "Shintoismus",
    "Shintoism",
    "Taoismus",
    "Taoism",
    "Daoismus",
    "Daoism",
    "Zoroastrismus",
    "Zoroastrianism",
    "Orthodox Christianity",
    "Orthodoxie",
)


def _word(c):
    # Combining marks and connector punctuation belong to a word too. This avoids
    # matching a bare term inside a decomposed or underscore-connected identifier.
    return c.isalnum() or c == "_" or unicodedata.category(c).startswith("M")


class ReligiousTerminology:
    """Operator-configurable literal terms; no user supplied regular expressions.

    At most 256 terms, 80 code points each, 2M input code points and 100k matches.
    Case folding and OCR line dehyphenation retain exact original source offsets.
    Configuration is trusted operator policy, not per-request model output.
    """

    def __init__(self, terms=DEFAULT_TERMS):
        if not isinstance(terms, (tuple, list)) or not 1 <= len(terms) <= 256:
            raise SafeError("invalid_terminology_configuration")
        if any(
            not isinstance(t, str)
            or not 1 <= len(t) <= 80
            or t != t.strip()
            or not all(c.isalpha() or c in " -'ʼáí" for c in t)
            for t in terms
        ):
            raise SafeError("invalid_terminology_configuration")
        self.terms = tuple(sorted({t.casefold() for t in terms}, key=lambda t: (-len(t), t)))

    def detect(self, text):
        if not isinstance(text, str) or len(text) > 2_000_000:
            raise SafeError("anonymization_text_limit")
        normalized, source_positions = dehyphenate(text)
        folded_parts, folded_positions = [], []
        for i, char in enumerate(normalized):
            part = char.casefold()
            folded_parts.append(part)
            folded_positions.extend([i] * len(part))
        folded = "".join(folded_parts)
        found = {}
        for term in self.terms:
            offset = 0
            while (left := folded.find(term, offset)) >= 0:
                right = left + len(term)
                offset = right
                if (left and _word(folded[left - 1])) or (
                    right < len(folded) and _word(folded[right])
                ):
                    continue
                # Never accept part of an expanding case fold (e.g. one s in ß).
                if (left and folded_positions[left - 1] == folded_positions[left]) or (
                    right < len(folded_positions)
                    and folded_positions[right - 1] == folded_positions[right]
                ):
                    continue
                start = source_positions[folded_positions[left]]
                end = source_positions[folded_positions[right - 1]] + 1
                found[(start, end)] = Entity(
                    start=start,
                    end=end,
                    label="RELIGION",
                    text=text[start:end],
                    source="deterministic",
                    model_revision=TERMINOLOGY_REVISION,
                    normalization="identity" if normalized == text else "dehyphenation-v1",
                )
                if len(found) > 100_000:
                    raise SafeError("invalid_entity_spans")
        return list(found.values())
