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
        from ocr.schemas import SafeError

        try:
            tokens = tuple(tokens)
            if any(type(t) is not int or not 0 <= t <= 257 for t in tokens):
                raise ValueError()
            return bytes(t for t in tokens if t < 256).decode("utf-8", errors="strict")
        except (TypeError, ValueError, UnicodeError):
            raise SafeError("tokenizer_invalid_or_truncated_utf8") from None

    def truncate_tokens(self, tokens, limit, *, keep="prefix"):
        """Return an explicit bounded prefix on a complete UTF-8 boundary.

        Invalid leading/interior byte sequences fail; only incomplete trailing
        codepoints are trimmed, as part of the requested truncation operation.
        """
        if type(limit) is not int or limit < 0 or keep not in ("prefix", "suffix"):
            raise ValueError("invalid_token_limit")
        prefix = (
            (tokens[:limit] if keep == "prefix" else tokens[-limit:]) if limit else []
        )
        if self._load() is not None:
            return prefix
        from ocr.schemas import SafeError

        # Validate the entire source before deciding which prefix to retain.
        self.decode(tokens)
        try:
            self.decode(prefix)
            return prefix
        except SafeError:
            payload = bytes(t for t in prefix if t < 256)
            if keep == "suffix":
                while payload and payload[0] & 0xC0 == 0x80:
                    payload = payload[1:]
                try:
                    payload.decode("utf-8", errors="strict")
                except UnicodeError:
                    raise SafeError("tokenizer_invalid_or_truncated_utf8") from None
                return list(payload) + ([257] if prefix and prefix[-1] == 257 else [])
            try:
                payload.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                if exc.reason != "unexpected end of data":
                    raise SafeError("tokenizer_invalid_or_truncated_utf8") from None
                return ([256] if prefix and prefix[0] == 256 else []) + list(
                    payload[: exc.start]
                )
            raise
