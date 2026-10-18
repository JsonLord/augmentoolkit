"""Chunking helpers that never provision corpora or tokenizers at runtime."""

import re
import threading


def sent_tokenize(text):
    try:
        from nltk.tokenize import sent_tokenize as cached_sentences

        return cached_sentences(text)
    except LookupError:
        # Untrained Punkt needs no corpus; preserve ordinary sentence chunking
        # when the optional language-specific corpus was not provisioned.
        from nltk.tokenize import PunktSentenceTokenizer

        return PunktSentenceTokenizer().tokenize(text)
    except ImportError:
        return re.split(r"(?<=[.!?])\s+", text)


class OfflineTokenizer:
    """Use a pre-provisioned tokenizer; otherwise conservatively count UTF-8 bytes.

    The byte fallback supports encode/decode for existing truncation callers. It
    overestimates typical BPE token counts and reserves two boundary tokens. It is
    not a claim of exact counts for an unavailable model-specific tokenizer.
    """

    def __init__(self, model):
        self.model = model
        self._tokenizer = None
        self._attempted = False
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if not self._attempted:
                try:
                    from transformers import AutoTokenizer

                    self._tokenizer = AutoTokenizer.from_pretrained(
                        self.model, local_files_only=True
                    )
                except (ImportError, OSError, ValueError, RuntimeError, KeyError):
                    self._tokenizer = None
                self._attempted = True
        return self._tokenizer

    def encode(self, text):
        tokenizer = self._load()
        return (
            tokenizer.encode(text)
            if tokenizer is not None
            else [256, *text.encode("utf-8"), 257]
        )

    def decode(self, tokens, **kwargs):
        tokenizer = self._load()
        if tokenizer is not None:
            return tokenizer.decode(tokens, **kwargs)
        return bytes(t for t in tokens if 0 <= t < 256).decode("utf-8", errors="ignore")
