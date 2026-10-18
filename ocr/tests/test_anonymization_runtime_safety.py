import ast
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest
from ocr.anonymization.atomic_output import write_candidate
from ocr.schemas import SafeError

ROOT = Path(__file__).parents[2]


def offline_helpers():
    spec = importlib.util.spec_from_file_location(
        "offline_chunking_helpers", ROOT / "augmentoolkit/utils/offline_chunking.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("stage", ["write", "flush", "fsync", "link"])
def test_atomic_candidate_failure_has_no_partial_target_or_staging_file(
    tmp_path, monkeypatch, stage
):
    import ocr.anonymization.atomic_output as module

    if stage in ("write", "flush"):
        original = module.os.fdopen

        class BrokenStream:
            def __init__(self, fd, mode):
                self.real = original(fd, mode)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.real.__exit__(*args)

            def write(self, data):
                if stage == "write":
                    self.real.write(data[:3])
                    raise OSError("synthetic disk failure")
                return self.real.write(data)

            def flush(self):
                if stage == "flush":
                    raise OSError("synthetic disk failure")

        monkeypatch.setattr(module.os, "fdopen", BrokenStream)
    else:
        monkeypatch.setattr(module.os, stage, Mock(side_effect=OSError("synthetic disk failure")))
    with pytest.raises(SafeError, match="anonymization_candidate_write_failed"):
        write_candidate(tmp_path / "candidate.json", b'{"complete":true}')
    assert not list(tmp_path.iterdir())


def test_publication_is_complete_atomic_private_and_no_overwrite(tmp_path, monkeypatch):
    import ocr.anonymization.atomic_output as module

    target = tmp_path / "candidate.json"
    data = b'{"complete":true}'
    original = module.os.link

    def inspect_link(source, dest):
        assert not target.exists()
        assert Path(source).read_bytes() == data
        assert Path(source).stat().st_mode & 0o777 == 0o600
        original(source, dest)

    monkeypatch.setattr(module.os, "link", inspect_link)
    write_candidate(target, data)
    assert target.read_bytes() == data and len(list(tmp_path.iterdir())) == 1
    monkeypatch.setattr(module.os, "link", original)
    with pytest.raises(SafeError):
        write_candidate(target, b"replacement")
    assert target.read_bytes() == data


def test_symlink_or_racing_writer_cannot_be_overwritten(tmp_path, monkeypatch):
    import ocr.anonymization.atomic_output as module

    original = module.os.link
    target, other = tmp_path / "candidate.json", tmp_path / "other"
    other.write_bytes(b"existing")
    target.symlink_to(other)
    with pytest.raises(SafeError):
        write_candidate(target, b"replacement")
    assert other.read_bytes() == b"existing"
    target.unlink()

    def racing_link(source, dest):
        Path(dest).write_bytes(b"winner")
        original(source, dest)

    monkeypatch.setattr(module.os, "link", racing_link)
    with pytest.raises(SafeError):
        write_candidate(target, b"loser")
    assert target.read_bytes() == b"winner"
    assert not list(tmp_path.glob(".anonymization-*"))


def test_chunking_import_does_not_download_nltk_or_model_files(monkeypatch):
    module = offline_helpers()
    fake_nltk = types.ModuleType("nltk")
    fake_nltk.download = Mock(side_effect=AssertionError("no runtime download"))
    loader = Mock(side_effect=AssertionError("no import-time model load"))
    monkeypatch.setitem(sys.modules, "nltk", fake_nltk)
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        types.SimpleNamespace(AutoTokenizer=types.SimpleNamespace(from_pretrained=loader)),
    )
    # Import the actual legacy module with optional format packages absent; no
    # extraction/backend/package download is needed to exercise ordinary chunking.
    monkeypatch.setitem(sys.modules, "pdf2image", None)
    spec = importlib.util.spec_from_file_location(
        "private_chunking_runtime_test", ROOT / "generation/core_components/chunking.py"
    )
    chunking = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    spec.loader.exec_module(chunking)
    fake_nltk.download.assert_not_called()
    loader.assert_not_called()
    chunking.count_tokens = lambda text: len(text.split())
    text = "Ordinary public text for a useful chunking smoke test that contains more than fifty characters."
    assert (
        chunking.chunking_algorithm_str(content=text, source_name="public.txt")[0]["text"] == text
    )
    # No syntactic import-time download call may reappear in this module.
    tree = ast.parse((ROOT / "generation/core_components/chunking.py").read_text())
    assert not any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "download"
        for n in ast.walk(tree)
    )
    assert module.OfflineTokenizer


def test_missing_cached_tokenizer_uses_reversible_conservative_fallback(monkeypatch):
    module = offline_helpers()
    loader = Mock(side_effect=OSError("cache unavailable"))
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        types.SimpleNamespace(AutoTokenizer=types.SimpleNamespace(from_pretrained=loader)),
    )
    tokenizer = module.OfflineTokenizer("operator-provisioned-model")
    text = "Ein gewöhnlicher Text."
    tokens = tokenizer.encode(text)
    assert len(tokens) == len(text.encode()) + 2
    assert tokenizer.decode(tokens) == text
    assert tokenizer.encode(text) == tokens
    loader.assert_called_once_with("operator-provisioned-model", local_files_only=True)


def test_cached_tokenizer_and_missing_nltk_corpus_preserve_chunking(monkeypatch):
    module = offline_helpers()
    backend = types.SimpleNamespace(
        encode=lambda text: [1, 2], decode=lambda tokens, **kw: "decoded"
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        types.SimpleNamespace(
            AutoTokenizer=types.SimpleNamespace(from_pretrained=Mock(return_value=backend))
        ),
    )
    tokenizer = module.OfflineTokenizer("cached")
    assert tokenizer.encode("text") == [1, 2] and tokenizer.decode([1, 2]) == "decoded"
    tokenize = types.ModuleType("nltk.tokenize")
    tokenize.sent_tokenize = Mock(side_effect=LookupError("corpus absent"))
    tokenize.PunktSentenceTokenizer = lambda: types.SimpleNamespace(
        tokenize=lambda text: ["First.", "Second."]
    )
    monkeypatch.setitem(sys.modules, "nltk.tokenize", tokenize)
    assert module.sent_tokenize("First. Second.") == ["First.", "Second."]


def test_short_write_never_publishes_partial_candidate(tmp_path, monkeypatch):
    import ocr.anonymization.atomic_output as module

    original = module.os.fdopen

    class ShortStream:
        def __init__(self, fd, mode):
            self.real = original(fd, mode)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.real.__exit__(*args)

        def write(self, data):
            return self.real.write(data[:3])

    monkeypatch.setattr(module.os, "fdopen", ShortStream)
    with pytest.raises(SafeError, match="anonymization_candidate_write_failed"):
        write_candidate(tmp_path / "candidate.json", b'{"complete":true}')
    assert not list(tmp_path.iterdir())
