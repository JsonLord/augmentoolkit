import os
import time
from unittest.mock import patch

import pytest
from augmentoolkit.utils.anonymization import anonymize_text
from ocr.handoff import anonymization_handoff
from ocr.schemas import Block, OCRPageResult, OCRResult, SafeError

# 1. Synthetic dataset generation with span-aware evaluation
SYNTHETIC_CASES = [
    # German Personal Names
    {
        "text": "Lina Wolkenstein wohnt in Berlin.",
        "expected": "[PERSON] wohnt in [CITY].",
        "spans": [
            {"start": 0, "end": 16, "label": "PERSON", "text": "Lina Wolkenstein"},
            {"start": 26, "end": 32, "label": "CITY", "text": "Berlin"},
        ],
    },
    {
        "text": "Hans Müller kauft ein.",
        "expected": "[PERSON] kauft ein.",
        "spans": [{"start": 0, "end": 11, "label": "PERSON", "text": "Hans Müller"}],
    },
    {
        "text": "Das Auto von Dr. Klaus Schmidt.",
        "expected": "Das Auto von Dr. [PERSON].",
        "spans": [{"start": 17, "end": 30, "label": "PERSON", "text": "Klaus Schmidt"}],
    },
    {
        "text": "Friedrich Wilhelm geht nach Hause.",
        "expected": "[PERSON] geht nach Hause.",
        "spans": [{"start": 0, "end": 17, "label": "PERSON", "text": "Friedrich Wilhelm"}],
    },
    {
        "text": "Ich kenne Frau Anne-Marie Schneider nicht.",
        "expected": "Ich kenne Frau [PERSON] nicht.",
        "spans": [{"start": 15, "end": 35, "label": "PERSON", "text": "Anne-Marie Schneider"}],
    },
    # English Personal Names
    {
        "text": "John Doe is a good man.",
        "expected": "[PERSON] is a good man.",
        "spans": [{"start": 0, "end": 8, "label": "PERSON", "text": "John Doe"}],
    },
    {
        "text": "Alice and Bob went to the store.",
        "expected": "[PERSON] and [PERSON] went to the store.",
        "spans": [
            {"start": 0, "end": 5, "label": "PERSON", "text": "Alice"},
            {"start": 10, "end": 13, "label": "PERSON", "text": "Bob"},
        ],
    },
    {
        "text": "Is Mary Johnson there?",
        "expected": "Is [PERSON] there?",
        "spans": [{"start": 3, "end": 15, "label": "PERSON", "text": "Mary Johnson"}],
    },
    {
        "text": "Meeting with Mr. Charles Montgomery.",
        "expected": "Meeting with Mr. [PERSON].",
        "spans": [{"start": 17, "end": 35, "label": "PERSON", "text": "Charles Montgomery"}],
    },
    {
        "text": "Elizabeth Taylor was famous.",
        "expected": "[PERSON] was famous.",
        "spans": [{"start": 0, "end": 16, "label": "PERSON", "text": "Elizabeth Taylor"}],
    },
    # Cities and Locations
    {
        "text": "I live in New York.",
        "expected": "I live in [CITY].",
        "spans": [{"start": 10, "end": 18, "label": "CITY", "text": "New York"}],
    },
    {
        "text": "Der Zug nach München fährt ab.",
        "expected": "Der Zug nach [CITY] fährt ab.",
        "spans": [{"start": 13, "end": 20, "label": "CITY", "text": "München"}],
    },
    {
        "text": "Paris is beautiful in spring.",
        "expected": "[CITY] is beautiful in spring.",
        "spans": [{"start": 0, "end": 5, "label": "CITY", "text": "Paris"}],
    },
    {
        "text": "Flug von Frankfurt nach Tokyo.",
        "expected": "Flug von [CITY] nach [CITY].",
        "spans": [
            {"start": 9, "end": 18, "label": "CITY", "text": "Frankfurt"},
            {"start": 24, "end": 29, "label": "CITY", "text": "Tokyo"},
        ],
    },
    {
        "text": "Welcome to Los Angeles, California.",
        "expected": "Welcome to [CITY], [CITY].",
        "spans": [
            {"start": 11, "end": 22, "label": "CITY", "text": "Los Angeles"},
            {"start": 24, "end": 34, "label": "CITY", "text": "California"},
        ],
    },
    # Religious Affiliations
    {
        "text": "Ihre Religionszugehörigkeit ist evangelisch.",
        "expected": "Ihre Religionszugehörigkeit ist [RELIGION].",
        "spans": [{"start": 32, "end": 43, "label": "RELIGION", "text": "evangelisch"}],
    },
    {
        "text": "Er ist katholisch.",
        "expected": "Er ist [RELIGION].",
        "spans": [{"start": 7, "end": 17, "label": "RELIGION", "text": "katholisch"}],
    },
    {
        "text": "They practice Judaism.",
        "expected": "They practice [RELIGION].",
        "spans": [{"start": 14, "end": 21, "label": "RELIGION", "text": "Judaism"}],
    },
    {
        "text": "He is a devout Muslim.",
        "expected": "He is a devout [RELIGION].",
        "spans": [{"start": 15, "end": 21, "label": "RELIGION", "text": "Muslim"}],
    },
    {
        "text": "Buddhist monks live there.",
        "expected": "[RELIGION] monks live there.",
        "spans": [{"start": 0, "end": 8, "label": "RELIGION", "text": "Buddhist"}],
    },
    # Multiple entities per sentence
    {
        "text": "John Doe from New York met Lina Wolkenstein in Berlin.",
        "expected": "[PERSON] from [CITY] met [PERSON] in [CITY].",
        "spans": [
            {"start": 0, "end": 8, "label": "PERSON", "text": "John Doe"},
            {"start": 14, "end": 22, "label": "CITY", "text": "New York"},
            {"start": 27, "end": 43, "label": "PERSON", "text": "Lina Wolkenstein"},
            {"start": 47, "end": 53, "label": "CITY", "text": "Berlin"},
        ],
    },
    {
        "text": "David is Catholic and lives in Rome.",
        "expected": "[PERSON] is [RELIGION] and lives in [CITY].",
        "spans": [
            {"start": 0, "end": 5, "label": "PERSON", "text": "David"},
            {"start": 9, "end": 17, "label": "RELIGION", "text": "Catholic"},
            {"start": 31, "end": 35, "label": "CITY", "text": "Rome"},
        ],
    },
    # Repeated entities
    {
        "text": "John likes John's new car.",
        "expected": "[PERSON] likes [PERSON]'s new car.",
        "spans": [
            {"start": 0, "end": 4, "label": "PERSON", "text": "John"},
            {"start": 11, "end": 15, "label": "PERSON", "text": "John"},
        ],
    },
    {
        "text": "Berlin, oh Berlin, my favorite city.",
        "expected": "[CITY], oh [CITY], my favorite city.",
        "spans": [
            {"start": 0, "end": 6, "label": "CITY", "text": "Berlin"},
            {"start": 11, "end": 17, "label": "CITY", "text": "Berlin"},
        ],
    },
    # Ambiguous names and place names
    {
        "text": "Washington is the capital.",
        "expected": "[CITY] is the capital.",
        "spans": [{"start": 0, "end": 10, "label": "CITY", "text": "Washington"}],
    },
    {
        "text": "George Washington was the president.",
        "expected": "[PERSON] was the president.",
        "spans": [{"start": 0, "end": 17, "label": "PERSON", "text": "George Washington"}],
    },
    {
        "text": "I visited the Jordan river.",
        "expected": "I visited the [CITY] river.",
        "spans": [{"start": 14, "end": 20, "label": "CITY", "text": "Jordan"}],
    },
    {
        "text": "Michael Jordan is a basketball player.",
        "expected": "[PERSON] is a basketball player.",
        "spans": [{"start": 0, "end": 14, "label": "PERSON", "text": "Michael Jordan"}],
    },
    # Names split over lines (Simulating OCR artifacts)
    {
        "text": "Mr. Smith-\nson",
        "expected": "Mr. [PERSON]",
        "spans": [{"start": 4, "end": 14, "label": "PERSON", "text": "Smith-\nson"}],
    },
    {
        "text": "Ber-\nlin",
        "expected": "[CITY]",
        "spans": [{"start": 0, "end": 8, "label": "CITY", "text": "Ber-\nlin"}],
    },
    # OCR spelling errors
    {
        "text": "Jchn D0e went home.",
        "expected": "[PERSON] went home.",
        "spans": [{"start": 0, "end": 8, "label": "PERSON", "text": "Jchn D0e"}],
    },
    {
        "text": "Welcome to Nevv Y0rk.",
        "expected": "Welcome to [CITY].",
        "spans": [{"start": 11, "end": 20, "label": "CITY", "text": "Nevv Y0rk"}],
    },
    # Documents without sensitive entities
    {"text": "The cat sat on the mat.", "expected": "The cat sat on the mat.", "spans": []},
    {"text": "12345 is a number.", "expected": "12345 is a number.", "spans": []},
    {"text": "This is a simple test.", "expected": "This is a simple test.", "spans": []},
    # Email addresses, phone numbers, organizations (Unsupported)
    {
        "text": "My email is test@example.com.",
        "expected": "My email is [EMAIL].",
        "spans": [{"start": 12, "end": 28, "label": "EMAIL", "text": "test@example.com"}],
    },
    {
        "text": "Call me at 555-0199.",
        "expected": "Call me at [PHONE].",
        "spans": [{"start": 11, "end": 19, "label": "PHONE", "text": "555-0199"}],
    },
    {
        "text": "I work for Google Inc.",
        "expected": "I work for [ORGANIZATION].",
        "spans": [{"start": 11, "end": 22, "label": "ORGANIZATION", "text": "Google Inc."}],
    },
    {
        "text": "Contact info@company.de",
        "expected": "Contact [EMAIL]",
        "spans": [{"start": 8, "end": 23, "label": "EMAIL", "text": "info@company.de"}],
    },
    {
        "text": "The UN is an organization.",
        "expected": "The [ORGANIZATION] is an organization.",
        "spans": [{"start": 4, "end": 6, "label": "ORGANIZATION", "text": "UN"}],
    },
]


def tagger_wrapper(text):
    result_text = anonymize_text(text)
    return {"text": result_text, "tags": []}


class TestAnonymizationQualification:
    def evaluate_metrics(self):
        """
        Calculates precision, recall, F1, false positives, false negatives,
        and unredacted canary count with exact expected string evaluation.
        """
        entity_types = ["PERSON", "CITY", "RELIGION", "EMAIL", "PHONE", "ORGANIZATION"]
        metrics = {e: {"tp": 0, "fp": 0, "fn": 0} for e in entity_types}

        unredacted_canaries = {e: 0 for e in entity_types}
        total_time = 0

        for case in SYNTHETIC_CASES:
            original = case["text"]
            expected = case["expected"]
            spans = case["spans"]

            start_time = time.time()
            actual = anonymize_text(original)
            total_time += time.time() - start_time

            # Since the current engine cannot return explicit spans, we use the
            # expected Gold Standard to assert if the actual string output matches perfectly.
            # If the entire result string matches expected, all spans are True Positives.
            # If it does not, we flag it.
            if actual == expected:
                for span in spans:
                    metrics[span["label"]]["tp"] += 1
            else:
                # Track false negatives and leaking canaries.
                # If a required word from a span is still in the output, it's a clear miss (FN and Canary).
                for span in spans:
                    label = span["label"]
                    text = span["text"]
                    if text.lower() in actual.lower():
                        metrics[label]["fn"] += 1
                        unredacted_canaries[label] += 1
                    else:
                        # It was redacted, but output is still not matching 'expected'.
                        # E.g. substituted the wrong tag, or merged words incorrectly.
                        # This counts as a FN for the intended label since it didn't do the correct thing.
                        metrics[label]["fn"] += 1

                # False positives are extra tags that shouldn't be there
                actual_tag_counts = {e: actual.count(f"[{e}]") for e in entity_types}
                expected_tag_counts = {e: expected.count(f"[{e}]") for e in entity_types}
                for label in entity_types:
                    diff = actual_tag_counts[label] - expected_tag_counts[label]
                    if diff > 0:
                        metrics[label]["fp"] += diff

        results = {}
        for entity in entity_types:
            counts = metrics[entity]
            tp = counts["tp"]
            fp = counts["fp"]
            fn = counts["fn"]

            precision = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

            results[entity] = {
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "false_positives": fp,
                "false_negatives": fn,
                "unredacted_canaries": unredacted_canaries[entity],
            }

        results["avg_runtime_ms"] = (total_time / len(SYNTHETIC_CASES)) * 1000
        return results

    @pytest.mark.skipif(
        os.environ.get("RUN_OPTIONAL_OCR_INTEGRATION") != "1", reason="Opt-in real model evaluation"
    )
    def test_evaluate_synthetic_dataset(self):
        """
        Evaluate the synthetic dataset and print metrics for the qualification report.
        """
        results = self.evaluate_metrics()
        print("\n--- Evaluation Metrics ---")
        for k, v in results.items():
            print(f"{k}: {v}")
        print("--------------------------\n")

        assert results["PERSON"]["unredacted_canaries"] > 0

    # === Security and Fail-Closed Testing ===
    # BASELINE CHARACTERIZATION
    @patch("augmentoolkit.utils.anonymization.PRESIDIO_AVAILABLE", False)
    def test_baseline_nltk_fail_open_security_flaw(self):
        """
        BASELINE DEFECT: If NLTK fails, it currently returns the original text (fail-open flaw).
        """
        with patch(
            "augmentoolkit.utils.anonymization.word_tokenize",
            side_effect=Exception("Simulated failure"),
        ):
            original_text = "Lina Wolkenstein wohnt in Berlin."
            anonymized = anonymize_text(original_text)

            # This assert demonstrates the flaw: original text is returned.
            assert anonymized == original_text
            assert "Wolkenstein" in anonymized

    # ACCEPTANCE TESTS
    def test_acceptance_fail_closed_handoff(self):
        """
        ACCEPTANCE TEST: The handoff wrapper must raise SafeError if the tagger
        errors out, preventing silent release of unredacted text.
        """
        page = OCRPageResult(
            page_index=1,
            page_id="p1",
            text="Secret Data",
            markdown="Secret Data",
            image_width=800,
            image_height=100,
            blocks=[],
            sensitivity="D2",
            source="cpu_ocr",
            engine="mocked",
            provider_destination="local",
        )
        result = OCRResult(
            schema_version="1",
            job_id="j1",
            request_id="r1",
            file_id="f1",
            page_count=1,
            markdown="Secret Data",
            text="Secret Data",
            failures=[],
            status="completed",
            sensitivity="D2",
            pages=[page],
            stats={"duration": 1.0, "estimated_cost": 0.0},
        )

        def bad_tagger(text):
            raise Exception("Tagger completely failed")

        with pytest.raises(SafeError, match="anonymization_failed"):
            anonymization_handoff(result, bad_tagger)

    def test_acceptance_malformed_outputs_blocked(self):
        """
        ACCEPTANCE TEST: Empty or None strings passed to handoff must be safely handled
        by raising a SafeError or ensuring it doesn't fail open.
        """
        page = OCRPageResult(
            page_index=1,
            page_id="p1",
            text="",
            markdown="",
            image_width=800,
            image_height=100,
            blocks=[],
            sensitivity="D2",
            source="cpu_ocr",
            engine="mocked",
            provider_destination="local",
        )
        result = OCRResult(
            schema_version="1",
            job_id="j1",
            request_id="r1",
            file_id="f1",
            page_count=1,
            markdown="",
            text="",
            failures=[],
            status="completed",
            sensitivity="D2",
            pages=[page],
            stats={"duration": 1.0, "estimated_cost": 0.0},
        )

        def invalid_tagger(text):
            return "This string doesn't match the dict format"

        with pytest.raises(SafeError, match="invalid_anonymization_result"):
            anonymization_handoff(result, invalid_tagger)

    # === OCR-to-Anonymization Integration ===

    @pytest.mark.skipif(
        os.environ.get("RUN_OPTIONAL_OCR_INTEGRATION") != "1", reason="Opt-in OCR integration test"
    )
    def test_ocr_to_anonymization_integration(self):
        """
        Verify that a representative OCR result successfully passes through handoff.
        This test uses a deterministic mock result to avoid relying on external ML OCR tools.
        """
        ocr_text = "Lina Wolkenstein wohnt in Berlin. Ihre Religionszugehoerigkeit ist evangelisch."
        page = OCRPageResult(
            page_index=1,
            page_id="p1",
            text=ocr_text,
            markdown=ocr_text,
            image_width=800,
            image_height=100,
            blocks=[Block(id="b1", bbox=[0, 0, 10, 10], text=ocr_text, confidence=0.9)],
            sensitivity="D2",
            source="cpu_ocr",
            engine="mocked",
            provider_destination="local",
        )
        result = OCRResult(
            schema_version="1",
            job_id="j1",
            request_id="r1",
            file_id="f1",
            page_count=1,
            markdown=ocr_text,
            text=ocr_text,
            failures=[],
            status="completed",
            sensitivity="D2",
            pages=[page],
            stats={"duration": 1.0, "estimated_cost": 0.0},
        )

        handoff_result = anonymization_handoff(result, tagger_wrapper)

        assert len(handoff_result["pages"]) == 1
        page_res = handoff_result["pages"][0]

        # Verify required handoff structure and states
        assert page_res["release_review_required"] is True
        assert handoff_result["anonymous"] is False
        assert handoff_result["original_evidence_retained_by_caller"] is True

        # Check actual text replacement output
        anonymized_text = page_res["anonymized_text"]
        assert "Lina" not in anonymized_text or "Berlin" not in anonymized_text
