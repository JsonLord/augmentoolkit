"""No model download or inference dependency in boundary qualification."""

import json
from unittest.mock import Mock

import pytest
from ocr.anonymization import AnonymizationEngine, Entity
from ocr.anonymization.model import MODEL_REVISION
from ocr.anonymization.recognizer import GLiNERRecognizer
from ocr.anonymization.replacement import replace
from ocr.anonymization.schemas import LABELS
from ocr.handoff import anonymization_handoff
from ocr.schemas import Block, Failure, OCRPageResult, OCRResult, SafeError

CANARY = "SYNTHETIC_UNREDACTED_SECRET"


class StubRecognizer:
    local = True
    revision = MODEL_REVISION
    categories = LABELS

    def recognize(self, text):
        return (
            [
                Entity(
                    start=0,
                    end=len(text),
                    label="PERSON",
                    text=text,
                    source="multilingual_ner",
                    model_revision=self.revision,
                    score=0.9,
                )
            ]
            if text
            else []
        )


def evidence(level="D2", texts=(CANARY,)):
    pages = [
        OCRPageResult(
            page_index=i,
            page_id=CANARY,
            source="cpu_ocr",
            engine=CANARY,
            provider_destination=CANARY,
            text=t,
            markdown=CANARY,
            image_width=1,
            image_height=1,
            blocks=[Block(text=CANARY)],
            sensitivity=level,
        )
        for i, t in enumerate(texts)
    ]
    pages[0].alternatives = [pages[0].model_copy(update={"alternatives": []})]
    return OCRResult(
        job_id=CANARY,
        request_id=CANARY,
        status="completed",
        page_count=len(pages),
        sensitivity=level,
        text=CANARY,
        markdown=CANARY,
        pages=pages,
        failures=[],
    )


def candidate(level="D2"):
    return anonymization_handoff(evidence(level), AnonymizationEngine(StubRecognizer()))


@pytest.mark.parametrize("level", ["D0", "D1", "D2"])
def test_new_handoff_retains_classification_and_discards_reidentifying_metadata(level):
    source = evidence(level, (CANARY, CANARY))
    snapshot = source.model_dump_json()
    value = anonymization_handoff(source, AnonymizationEngine(StubRecognizer()))
    assert source.model_dump_json() == snapshot
    assert CANARY not in value.model_dump_json()
    assert len(value.pages) == 2
    assert value.sensitivity == level
    assert all(p.original_sensitivity == level for p in value.pages)
    assert value.release_review_required is True
    assert value.release_approved is False and value.anonymous is False
    assert value.automated_release_eligible is False


@pytest.mark.parametrize(
    "failure",
    [
        "exception",
        "none",
        "string",
        "extra",
        "wrong_slice",
        "invalid_score",
        "invalid_label",
        "negative",
        "past_end",
        "bool_offset",
        "nan_score",
    ],
)
def test_untrusted_model_outputs_never_become_candidates(failure, capsys):
    model = StubRecognizer()
    good = model.recognize(CANARY)[0].model_dump()
    if failure == "exception":
        model.recognize = Mock(side_effect=RuntimeError(CANARY))
    else:
        bad = {
            "none": None,
            "string": CANARY,
            "extra": [{**good, "preview": CANARY}],
            "wrong_slice": [{**good, "text": "wrong"}],
            "invalid_score": [{**good, "score": 1.1}],
            "invalid_label": [{**good, "label": "NAME"}],
            "negative": [{**good, "start": -1}],
            "past_end": [{**good, "end": 999999}],
            "bool_offset": [{**good, "start": False}],
            "nan_score": [{**good, "score": float("nan")}],
        }[failure]
        model.recognize = Mock(return_value=bad)
    with pytest.raises(SafeError) as error:
        AnonymizationEngine(model).handoff(evidence())
    assert CANARY not in str(error.value)
    assert CANARY not in str(capsys.readouterr())


def test_model_unavailable_fails_without_any_download(tmp_path):
    with pytest.raises(SafeError, match="anonymization_model_unavailable"):
        GLiNERRecognizer(tmp_path)


@pytest.mark.parametrize("attr,value", [("categories", ("PERSON",)), ("local", False)])
def test_unsupported_categories_or_external_recognizer_blocked(attr, value):
    model = StubRecognizer()
    setattr(model, attr, value)
    with pytest.raises(SafeError):
        AnonymizationEngine(model).handoff(evidence())


@pytest.mark.parametrize(
    "mutation", ["partial", "failure", "missing", "duplicate", "classification"]
)
def test_incomplete_or_inconsistent_evidence_fails_closed(mutation):
    result = evidence(texts=(CANARY, CANARY))
    if mutation == "partial":
        result.status = "partial"
    elif mutation == "failure":
        result.failures = [Failure(page_index=3, code="failed")]
    elif mutation == "missing":
        result.page_count += 1
    elif mutation == "duplicate":
        result.pages[1].page_index = 0
    else:
        result.pages[1].sensitivity = "D0"
    with pytest.raises(SafeError):
        AnonymizationEngine(StubRecognizer()).handoff(result)


def test_failure_on_second_page_returns_no_partial_candidate():
    model = StubRecognizer()
    model.recognize = Mock(side_effect=[model.recognize(CANARY), RuntimeError(CANARY)])
    with pytest.raises(SafeError, match="anonymization_failed"):
        AnonymizationEngine(model).handoff(evidence(texts=(CANARY, CANARY)))


def test_empty_predictions_require_review_and_never_claim_success():
    model = StubRecognizer()
    model.recognize = Mock(return_value=[])
    value = AnonymizationEngine(model).handoff(evidence())
    assert "empty_prediction" in value.review_reasons
    assert not value.automated_release_eligible and not value.release_approved


def test_bad_placeholders_block_transformation():
    with pytest.raises(SafeError, match="unsupported_required_categories"):
        replace(CANARY, [], {"PERSON": "unsafe"})


def test_untrusted_safe_error_cannot_smuggle_source_into_error_response():
    model = StubRecognizer()
    model.recognize = Mock(side_effect=SafeError(CANARY))
    with pytest.raises(SafeError, match="^anonymization_failed$"):
        AnonymizationEngine(model).handoff(evidence())


def test_model_copy_cannot_bypass_span_validation():
    forged = StubRecognizer().recognize(CANARY)[0].model_copy(update={"start": -1})
    with pytest.raises(SafeError, match="invalid_entity_spans"):
        replace(CANARY, [forged])


def test_model_manifest_corruption_is_rejected(tmp_path):
    from ocr.anonymization.model import FILES, MODEL_ID, file_hash, verify_cache

    for name in FILES:
        (tmp_path / name).write_text("synthetic artifact")
    manifest = dict(
        model=MODEL_ID,
        revision=MODEL_REVISION,
        files={name: file_hash(tmp_path / name) for name in FILES},
    )
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    # Authentic artifact hashes are pinned in the repository, so even a forged
    # local manifest with internally consistent hashes must fail.
    with pytest.raises(SafeError, match="anonymization_model_unavailable"):
        verify_cache(tmp_path)
    (tmp_path / FILES[0]).write_text("tampered")
    with pytest.raises(SafeError, match="anonymization_model_unavailable"):
        verify_cache(tmp_path)


def test_inference_without_runtime_is_sanitized(monkeypatch):
    import sys

    model = GLiNERRecognizer.__new__(GLiNERRecognizer)
    model._lock = __import__("threading").Lock()
    model.prompts = {k: k for k in LABELS}
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(SafeError, match="anonymization_inference_failed"):
        model.recognize(CANARY)


def test_legacy_pipeline_flag_blocks_before_processing_or_external_egress():
    # Execute the real chunking entry function without importing its legacy
    # module-level NLTK/tokenizer downloads. No data generation backend is needed.
    import ast
    import os
    from pathlib import Path

    path = Path(__file__).parents[2] / "generation/core_components/chunking.py"
    tree = ast.parse(path.read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "chunking_algorithm_str"
    )
    namespace = {"os": os, "SafeError": SafeError}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    with pytest.raises(SafeError, match="legacy_anonymization_requires_reviewed_workflow"):
        namespace["chunking_algorithm_str"](content=CANARY, anonymize=True)


def test_model_initialization_exception_is_sanitized(tmp_path, monkeypatch):
    import sys
    import types

    import ocr.anonymization.recognizer as module

    monkeypatch.setattr(module, "verify_cache", lambda path: path)
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(set_num_threads=Mock()))
    monkeypatch.setitem(
        sys.modules,
        "gliner",
        types.SimpleNamespace(
            GLiNER=types.SimpleNamespace(from_pretrained=Mock(side_effect=RuntimeError(CANARY)))
        ),
    )
    with pytest.raises(SafeError, match="^anonymization_model_unavailable$"):
        GLiNERRecognizer(tmp_path)


@pytest.mark.parametrize(
    "options",
    [
        dict(threshold="bad"),
        dict(threshold=float("nan")),
        dict(prompts=[]),
        dict(prompts={k: [] for k in LABELS}),
        dict(threads=True),
    ],
)
def test_invalid_runtime_configuration_is_sanitized(tmp_path, options):
    with pytest.raises(SafeError):
        GLiNERRecognizer(tmp_path, **options)


def test_cli_creates_private_classified_candidate_and_never_overwrites(
    tmp_path, monkeypatch, capsys
):
    import sys

    from ocr.anonymization import __main__ as cli

    source, output = tmp_path / "evidence.json", tmp_path / "candidate.json"
    source.write_text(evidence().model_dump_json())
    monkeypatch.setattr(cli, "GLiNERRecognizer", lambda cache: StubRecognizer())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "anonymization",
            "--model-cache",
            str(tmp_path),
            "--input",
            str(source),
            "--output",
            str(output),
        ],
    )
    cli.main()
    original = output.read_bytes()
    assert CANARY.encode() not in original
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(original)["sensitivity"] == "D2"
    with pytest.raises(SystemExit, match="anonymization_candidate_failed"):
        cli.main()
    assert output.read_bytes() == original
    assert CANARY not in str(capsys.readouterr())


def test_cli_failure_never_writes_a_candidate(tmp_path, monkeypatch):
    import sys

    from ocr.anonymization import __main__ as cli

    source, output = tmp_path / "evidence.json", tmp_path / "candidate.json"
    source.write_text(evidence().model_dump_json())
    monkeypatch.setattr(cli, "GLiNERRecognizer", Mock(side_effect=RuntimeError(CANARY)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "anonymization",
            "--model-cache",
            str(tmp_path),
            "--input",
            str(source),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit, match="anonymization_candidate_failed"):
        cli.main()
    assert not output.exists()
