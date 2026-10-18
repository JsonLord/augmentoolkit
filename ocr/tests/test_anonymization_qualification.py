import time
from unittest.mock import patch

import pytesseract
import pytest
from augmentoolkit.utils.anonymization import anonymize_text
from ocr.handoff import anonymization_handoff
from ocr.schemas import Block, OCRPageResult, OCRResult, SafeError
from PIL import Image, ImageDraw

# 1. Synthetic dataset generation
SYNTHETIC_CASES = [
    # German Personal Names
    {"text": "Lina Wolkenstein wohnt in Berlin.", "expected": "[PERSON] wohnt in [CITY].", "entities": {"PERSON": 1, "CITY": 1, "RELIGION": 0}, "canaries": ["Lina Wolkenstein", "Berlin"]},
    {"text": "Hans Müller kauft ein.", "expected": "[PERSON] kauft ein.", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["Hans Müller"]},
    {"text": "Das Auto von Dr. Klaus Schmidt.", "expected": "Das Auto von Dr. [PERSON].", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["Klaus Schmidt"]},
    {"text": "Friedrich Wilhelm geht nach Hause.", "expected": "[PERSON] geht nach Hause.", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["Friedrich Wilhelm"]},
    {"text": "Ich kenne Frau Anne-Marie Schneider nicht.", "expected": "Ich kenne Frau [PERSON] nicht.", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["Anne-Marie Schneider"]},

    # English Personal Names
    {"text": "John Doe is a good man.", "expected": "[PERSON] is a good man.", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["John Doe"]},
    {"text": "Alice and Bob went to the store.", "expected": "[PERSON] and [PERSON] went to the store.", "entities": {"PERSON": 2, "CITY": 0, "RELIGION": 0}, "canaries": ["Alice", "Bob"]},
    {"text": "Is Mary Johnson there?", "expected": "Is [PERSON] there?", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["Mary Johnson"]},
    {"text": "Meeting with Mr. Charles Montgomery.", "expected": "Meeting with Mr. [PERSON].", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["Charles Montgomery"]},
    {"text": "Elizabeth Taylor was famous.", "expected": "[PERSON] was famous.", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["Elizabeth Taylor"]},

    # Cities and Locations
    {"text": "I live in New York.", "expected": "I live in [CITY].", "entities": {"PERSON": 0, "CITY": 1, "RELIGION": 0}, "canaries": ["New York"]},
    {"text": "Der Zug nach München fährt ab.", "expected": "Der Zug nach [CITY] fährt ab.", "entities": {"PERSON": 0, "CITY": 1, "RELIGION": 0}, "canaries": ["München"]},
    {"text": "Paris is beautiful in spring.", "expected": "[CITY] is beautiful in spring.", "entities": {"PERSON": 0, "CITY": 1, "RELIGION": 0}, "canaries": ["Paris"]},
    {"text": "Flug von Frankfurt nach Tokyo.", "expected": "Flug von [CITY] nach [CITY].", "entities": {"PERSON": 0, "CITY": 2, "RELIGION": 0}, "canaries": ["Frankfurt", "Tokyo"]},
    {"text": "Welcome to Los Angeles, California.", "expected": "Welcome to [CITY], [CITY].", "entities": {"PERSON": 0, "CITY": 2, "RELIGION": 0}, "canaries": ["Los Angeles", "California"]},

    # Religious Affiliations
    {"text": "Ihre Religionszugehörigkeit ist evangelisch.", "expected": "Ihre Religionszugehörigkeit ist [RELIGION].", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 1}, "canaries": ["evangelisch"]},
    {"text": "Er ist katholisch.", "expected": "Er ist [RELIGION].", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 1}, "canaries": ["katholisch"]},
    {"text": "They practice Judaism.", "expected": "They practice [RELIGION].", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 1}, "canaries": ["Judaism"]},
    {"text": "He is a devout Muslim.", "expected": "He is a devout [RELIGION].", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 1}, "canaries": ["Muslim"]},
    {"text": "Buddhist monks live there.", "expected": "[RELIGION] monks live there.", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 1}, "canaries": ["Buddhist"]},

    # Multiple entities per sentence
    {"text": "John Doe from New York met Lina Wolkenstein in Berlin.", "expected": "[PERSON] from [CITY] met [PERSON] in [CITY].", "entities": {"PERSON": 2, "CITY": 2, "RELIGION": 0}, "canaries": ["John Doe", "New York", "Lina Wolkenstein", "Berlin"]},
    {"text": "David is Catholic and lives in Rome.", "expected": "[PERSON] is [RELIGION] and lives in [CITY].", "entities": {"PERSON": 1, "CITY": 1, "RELIGION": 1}, "canaries": ["David", "Catholic", "Rome"]},

    # Repeated entities
    {"text": "John likes John's new car.", "expected": "[PERSON] likes [PERSON]'s new car.", "entities": {"PERSON": 2, "CITY": 0, "RELIGION": 0}, "canaries": ["John", "John"]},
    {"text": "Berlin, oh Berlin, my favorite city.", "expected": "[CITY], oh [CITY], my favorite city.", "entities": {"PERSON": 0, "CITY": 2, "RELIGION": 0}, "canaries": ["Berlin", "Berlin"]},

    # Ambiguous names and place names
    {"text": "Washington is the capital.", "expected": "[CITY] is the capital.", "entities": {"PERSON": 0, "CITY": 1, "RELIGION": 0}, "canaries": ["Washington"]},
    {"text": "George Washington was the president.", "expected": "[PERSON] was the president.", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["George Washington"]},
    {"text": "I visited the Jordan river.", "expected": "I visited the [CITY] river.", "entities": {"PERSON": 0, "CITY": 1, "RELIGION": 0}, "canaries": ["Jordan"]},
    {"text": "Michael Jordan is a basketball player.", "expected": "[PERSON] is a basketball player.", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["Michael Jordan"]},

    # Names split over lines (Simulating OCR artifacts)
    {"text": "Mr. Smith-\nson", "expected": "Mr. [PERSON]", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["Smith-\nson"]},
    {"text": "Ber-\nlin", "expected": "[CITY]", "entities": {"PERSON": 0, "CITY": 1, "RELIGION": 0}, "canaries": ["Ber-\nlin"]},

    # OCR spelling errors
    {"text": "Jchn D0e went home.", "expected": "[PERSON] went home.", "entities": {"PERSON": 1, "CITY": 0, "RELIGION": 0}, "canaries": ["Jchn D0e"]},
    {"text": "Welcome to Nevv Y0rk.", "expected": "Welcome to [CITY].", "entities": {"PERSON": 0, "CITY": 1, "RELIGION": 0}, "canaries": ["Nevv Y0rk"]},

    # Documents without sensitive entities
    {"text": "The cat sat on the mat.", "expected": "The cat sat on the mat.", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 0}, "canaries": []},
    {"text": "12345 is a number.", "expected": "12345 is a number.", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 0}, "canaries": []},
    {"text": "This is a simple test.", "expected": "This is a simple test.", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 0}, "canaries": []},

    # Email addresses, phone numbers, organizations (Unsupported)
    {"text": "My email is test@example.com.", "expected": "My email is test@example.com.", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 0}, "canaries": ["test@example.com"]},
    {"text": "Call me at 555-0199.", "expected": "Call me at 555-0199.", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 0}, "canaries": ["555-0199"]},
    {"text": "I work for Google Inc.", "expected": "I work for Google Inc.", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 0}, "canaries": ["Google Inc."]},
    {"text": "Contact info@company.de", "expected": "Contact info@company.de", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 0}, "canaries": ["info@company.de"]},
    {"text": "The UN is an organization.", "expected": "The UN is an organization.", "entities": {"PERSON": 0, "CITY": 0, "RELIGION": 0}, "canaries": ["UN"]}
]

def tagger_wrapper(text):
    result_text = anonymize_text(text)
    return {"text": result_text, "tags": []}

class TestAnonymizationQualification:
    def evaluate_metrics(self):
        """
        Calculates precision, recall, F1, false positives, false negatives,
        and unredacted canary count for the existing implementation.
        """
        metrics = {
            "PERSON": {"tp": 0, "fp": 0, "fn": 0},
            "CITY": {"tp": 0, "fp": 0, "fn": 0},
            "RELIGION": {"tp": 0, "fp": 0, "fn": 0}
        }

        unredacted_canaries = 0
        total_time = 0

        for case in SYNTHETIC_CASES:
            original = case["text"]

            start_time = time.time()
            actual = anonymize_text(original)
            total_time += time.time() - start_time

            # Simple heuristic counting of tags in actual vs expected
            for entity in metrics.keys():
                expected_count = case["expected"].count(f"[{entity}]")
                actual_count = actual.count(f"[{entity}]")

                # Assume any match we expected is a True Positive, any extra is a False Positive,
                # and any missed is a False Negative.
                metrics[entity]["tp"] += min(expected_count, actual_count)
                if actual_count > expected_count:
                    metrics[entity]["fp"] += (actual_count - expected_count)
                if actual_count < expected_count:
                    metrics[entity]["fn"] += (expected_count - actual_count)

            # Check for unredacted canaries
            for canary in case["canaries"]:
                if canary.lower() in actual.lower():
                    unredacted_canaries += 1

        results = {}
        for entity, counts in metrics.items():
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
                "false_negatives": fn
            }

        results["unredacted_canaries"] = unredacted_canaries
        results["avg_runtime_ms"] = (total_time / len(SYNTHETIC_CASES)) * 1000
        return results

    def test_evaluate_synthetic_dataset(self):
        """
        Evaluate the synthetic dataset and print metrics for the qualification report.
        """
        results = self.evaluate_metrics()
        print("\n--- Evaluation Metrics ---")
        for k, v in results.items():
            print(f"{k}: {v}")
        print("--------------------------\n")

        # We assert the number of canaries leaked is > 0, proving it's NOT safe for release.
        assert results["unredacted_canaries"] > 0

    # === Security and Fail-Closed Testing ===

    @patch("augmentoolkit.utils.anonymization.PRESIDIO_AVAILABLE", False)
    def test_nltk_fail_open_security_flaw(self):
        """
        Simulation: Recognition exception (NLTK)
        If NLTK fails, it returns the original text (fail-open), which is a security flaw.
        We simulate an exception in word_tokenize to trigger the exception block.
        """
        with patch("augmentoolkit.utils.anonymization.word_tokenize", side_effect=Exception("Simulated failure")):
            original_text = "Lina Wolkenstein wohnt in Berlin."
            anonymized = anonymize_text(original_text)

            # The original text is returned unredacted! This is a NO-GO.
            assert anonymized == original_text
            assert "Wolkenstein" in anonymized

    @patch("augmentoolkit.utils.anonymization.PRESIDIO_AVAILABLE", False)
    def test_missing_nltk_resources(self):
        """
        Simulation: Missing NLTK resources
        If NLTK is supposed to run but word_tokenize fails because download throws
        and then it proceeds and chunks raise an exception. NLTK falls open.
        Instead of simulating download failure only, we patch an inner function
        and expect fallback.
        """
        # If chunks fail, we return original text.
        with patch("augmentoolkit.utils.anonymization.ne_chunk", side_effect=Exception("Missing Resource")):
            original_text = "Alice goes to Paris."
            anonymized = anonymize_text(original_text)

            assert anonymized == original_text
            assert "Alice" in anonymized

    @patch("augmentoolkit.utils.anonymization.PRESIDIO_AVAILABLE", True)
    def test_missing_presidio_dependency(self):
        """
        Simulation: Missing Presidio dependency
        """
        # When Presidio is available but initialization fails
        # It logs a warning, doesn't crash, and falls back to NLTK.
        with patch("augmentoolkit.utils.anonymization.AnalyzerEngine", side_effect=Exception("Presidio init failed"), create=True):
            # Also patch NLTK to prove we reached the fallback
            with patch("augmentoolkit.utils.anonymization.anonymize_text_nltk", return_value="FALLBACK_NLTK"):
                anonymized = anonymize_text("Some text")
                assert anonymized == "FALLBACK_NLTK"

    def test_empty_and_malformed_outputs(self):
        """
        Simulation: Empty and malformed recognizer outputs.
        """
        assert anonymize_text("") == ""
        assert anonymize_text(None) is None

    def test_partially_detected_entities(self):
        """
        Simulation: Partially detected entities (e.g. OCR split line).
        """
        original_text = "Lina Wolken-\nstein lives here."
        actual = anonymize_text(original_text)

        # Leaks split entities.
        assert "Wolken" in actual or "stein" in actual

    def test_ocr_handoff_with_failing_tagger(self):
        """
        Check that `anonymization_handoff` prevents silent release of text
        if the tagger raises an exception.
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
            engine="tesseract",
            provider_destination="local"
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
            stats={"duration": 1.0, "estimated_cost": 0.0}
        )

        def bad_tagger(text):
            raise Exception("Tagger completely failed")

        with pytest.raises(SafeError, match="anonymization_failed"):
            anonymization_handoff(result, bad_tagger)

    # === OCR-to-Anonymization Integration ===

    def test_ocr_to_anonymization_integration(self, tmp_path):
        """
        Create a synthetic image, run OCR, and pass through handoff.
        """
        text = "Lina Wolkenstein wohnt in Berlin. Ihre Religionszugehoerigkeit ist evangelisch."
        img_path = tmp_path / "test_ocr.png"
        img = Image.new('RGB', (800, 100), color=(255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((10, 10), text, fill=(0, 0, 0))
        img.save(img_path)

        ocr_text = pytesseract.image_to_string(Image.open(img_path)).strip()

        page = OCRPageResult(
            page_index=1,
            page_id="p1",
            text=ocr_text,
            markdown=ocr_text,
            image_width=800,
            image_height=100,
            blocks=[Block(id="b1", bbox=[0,0,10,10], text=ocr_text, confidence=0.9)],
            sensitivity="D2",
            source="cpu_ocr",
            engine="tesseract",
            provider_destination="local"
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
            stats={"duration": 1.0, "estimated_cost": 0.0}
        )

        handoff_result = anonymization_handoff(result, tagger_wrapper)

        assert len(handoff_result["pages"]) == 1
        anonymized_text = handoff_result["pages"][0]["anonymized_text"]

        print("Anonymized OCR text:", anonymized_text)
        assert handoff_result["original_evidence_retained_by_caller"] is True
