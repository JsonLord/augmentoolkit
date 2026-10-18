"""Replacement correctness is independent of model recognition accuracy."""

import pytest
from ocr.anonymization.detectors import detect
from ocr.anonymization.model import MODEL_REVISION
from ocr.anonymization.replacement import replace
from ocr.anonymization.schemas import Entity
from ocr.schemas import SafeError


def entity(text, label, start, end):
    return Entity(
        start=start,
        end=end,
        label=label,
        text=text[start:end],
        source="multilingual_ner",
        model_revision=MODEL_REVISION,
        score=0.8,
    )


def test_every_qualification_gold_span_replaces_exactly():
    # Preserve the merged historical corpus byte-for-byte, and use its gold spans
    # to measure replacement alone. This is NOT a recognition-accuracy test.
    from ocr.tests.test_anonymization_qualification import SYNTHETIC_CASES

    for case in SYNTHETIC_CASES:
        predictions = [
            entity(case["text"], s["label"], s["start"], s["end"]) for s in case["spans"]
        ]
        output, tags = replace(case["text"], predictions)
        # Historical Google Inc. fixture includes the terminal period in its
        # gold span but also expects a period outside replacement. Preserve the
        # fixture; source spans take precedence over that inconsistent string.
        expected = case["expected"]
        if case["text"] == "I work for Google Inc.":
            expected = "I work for [ORGANIZATION]"
        assert output == expected
        assert len(tags) == len(predictions)
        assert all("text" not in t.model_dump() for t in tags)


def test_repeated_umlaut_hyphenated_broken_words_preserve_other_spacing():
    text = "Änne-Marie  grüßt\nÄnne-Marie.\n\nBer-\nlin!"
    spans = [
        entity(text, "PERSON", 0, 10),
        entity(text, "PERSON", 18, 28),
        entity(text, "CITY", 31, 39),
    ]
    output, _ = replace(text, spans)
    assert output == "[PERSON]  grüßt\n[PERSON].\n\n[CITY]!"


def test_containment_and_duplicate_policy():
    text = "Mary Jane Doe"
    spans = [
        entity(text, "PERSON", 0, len(text)),
        entity(text, "PERSON", 5, len(text)),
        entity(text, "PERSON", 0, len(text)),
    ]
    output, tags = replace(text, spans)
    assert output == "[PERSON]" and len(tags) == 1


def test_crossing_or_conflicting_overlap_blocks_whole_transformation():
    text = "Mary Jane Doe"
    with pytest.raises(SafeError, match="ambiguous_entity_overlap"):
        replace(text, [entity(text, "PERSON", 0, 9), entity(text, "PERSON", 5, len(text))])
    with pytest.raises(SafeError, match="ambiguous_entity_overlap"):
        replace(text, [entity(text, "PERSON", 0, 4), entity(text, "CITY", 0, 4)])


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Mail: föö@example.de.\nCall +49 30 12345678!", "Mail: [EMAIL].\nCall [PHONE]!"),
        ("test@example.com and 555-0199.", "[EMAIL] and [PHONE]."),
        ("12345 is a number.", "12345 is a number."),
    ],
)
def test_contact_patterns(text, expected):
    output, tags = replace(text, detect(text))
    assert output == expected
    assert all(t.score is None for t in tags)


def test_dehyphenation_preserves_original_character_mapping():
    from ocr.anonymization.normalization import dehyphenate

    text = "Än-\nne in Ber-\r\nlin, and Anne-Marie."
    normalized, positions = dehyphenate(text)
    assert normalized == "Änne in Berlin, and Anne-Marie."
    start = normalized.index("Berlin")
    end = start + len("Berlin")
    assert text[positions[start] : positions[end - 1] + 1] == "Ber-\r\nlin"
    assert all(normalized[i] == text[p] for i, p in enumerate(positions))


def test_recognizer_maps_normalized_predictions_without_inventing_offsets():
    from ocr.anonymization.recognizer import GLiNERRecognizer

    model = GLiNERRecognizer.__new__(GLiNERRecognizer)

    def view(text):
        if text != "Berlin":
            return []
        return [entity(text, "CITY", 0, 6)]

    model._recognize_view = view
    found = model.recognize("Ber-\nlin")
    assert len(found) == 1 and found[0].text == "Ber-\nlin"
    assert found[0].normalization == "dehyphenation-v1"
    assert replace("Ber-\nlin", found)[0] == "[CITY]"


def test_entity_level_evaluator_separates_labels_coverage_and_partial_masking():
    from ocr.anonymization.evaluate import score_cases

    class Recognition:
        def recognize(self, text):
            return [entity(text, "ORGANIZATION", 0, 3)]

    case = dict(
        text="ABC DEF",
        expected="[PERSON]",
        spans=[dict(start=0, end=7, label="PERSON", text="ABC DEF")],
    )
    report = score_cases(Recognition(), [case])
    assert report["combined_entity_metrics"]["PERSON"]["fn"] == 1
    assert report["combined_entity_metrics"]["ORGANIZATION"]["fp"] == 1
    assert report["unredacted_canaries"]["PERSON"] == 1
    assert report["literal_unredacted_canaries"]["PERSON"] == 0
    assert report["qualification_passed"] is False
