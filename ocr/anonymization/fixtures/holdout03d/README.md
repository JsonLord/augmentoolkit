# Gate 03d independent qualification corpus

Frozen before first model evaluation. 160 individually authored synthetic cases,
80 German and 80 English, in ten strata of sixteen: explicit affiliation,
non-affiliation, belief names, general topics, negation/quotation, contextual
ambiguity, OCR corruption, multiple entities, varied formats/repetition and
negative controls. No real personal data. These are authored by the implementing
agent, not an externally blinded or population-representative sample.

Gold spans protect religious terminology and explicit status phrases as text;
they do not assert any person's religion. Religion names in general topics,
quotations and negations are protected. Status phrases include their negation
where the phrase expresses non-affiliation. Ambiguous adjectives in a clearly
religious context are protected; mathematical/marketing metaphors are negatives.
Deliberately corrupted OCR spellings retain their exact source spans. Person,
geography, organization and contact spans use the existing category meanings.

`cases.json` and `freeze.json` are the immutable first-evaluation inputs. No model
inference was used to create or annotate them. The new configuration was selected
using only the previous 40+10+120 development cases. The first result must be kept
regardless of leakage, false positives or blocked documents. Once inspected,
this corpus is development evidence for any future tuning. A zero-canary finite
sample cannot establish universal privacy safety. Automatic release stays off.

Reproduce in a checkout of the recorded freeze commit, using the pinned locked
CPU dependencies and independently integrity-verified local model cache:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
  python -m ocr.anonymization.qualify03d \
  --cache /private/model-cache --output /private/new-qualification-result.json
```

The command refuses to overwrite an existing result and returns nonzero on a
failed recognition gate, after saving the full first result and per-case evidence.
