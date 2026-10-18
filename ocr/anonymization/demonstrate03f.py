"""Pinned-model utility demo on EXISTING synthetic development cases only.

The isolated host simulates a human choosing existing gold spans; these are not
model predictions or a new holdout. No completed release review is manufactured.
"""

import argparse
import hashlib
import importlib.metadata
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ocr.schemas import SafeError
from ocr.tests.fixtures.correction_host import CorrectionHostFixture
from ocr.tests.test_anonymization_security import evidence

from .approval_ledger import SQLiteApprovalLedger
from .atomic_output import write_candidate
from .corrections import CorrectionEdit, CorrectionService, commitment, prepare_review
from .engine import AnonymizationEngine
from .host_adapter import HostAuthorizationBridge
from .recognizer import GLiNERRecognizer
from .release_policy import digest


def coverage(candidate, case):
    if candidate is None:
        return None
    tags = candidate.pages[0].tags
    return all(
        any(t.start <= i < t.end for t in tags)
        for span in case["spans"]
        for i in range(span["start"], span["end"])
        if case["text"][i].isalnum()
    )


def run(cache, output):
    if Path(output).exists():
        raise FileExistsError("correction_demo_exists")
    denied = []

    def offline(event, args):
        if event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
            denied.append(event)
            raise PermissionError("correction_demo_network_disabled")

    sys.addaudithook(offline)
    source = Path(__file__).parent / "fixtures/holdout03d/cases.json"
    cases = json.loads(source.read_bytes())
    selected = {1, 2, 3, 55, 63, 78, 125, 127, 137}
    start = time.perf_counter()
    model = GLiNERRecognizer(cache)
    loading_ms = (time.perf_counter() - start) * 1000
    rows = []
    for case in cases:
        number = int(case["id"].split("-")[-1])
        if number not in selected:
            continue
        start = time.perf_counter()
        context = prepare_review(
            AnonymizationEngine(model),
            evidence("D2", (case["text"],)),
            review_ref=case["id"],
            environment_id="synthetic-room",
            expires_at=int(time.time()) + 600,
        )
        inference_ms = (time.perf_counter() - start) * 1000
        host = CorrectionHostFixture(context, {0: case["text"]}, separate_approver=True)
        service = CorrectionService(host)
        candidate = context.material.candidate
        before = coverage(candidate, case)
        original = (
            "blocked_conflicting_spans"
            if candidate is None
            else "complete_no_measured_leakage"
            if before
            else "candidate_partial_mask"
        )
        edits = []
        if number not in (1, 2, 3):
            for span in case["spans"]:
                if not (number == 125 and span["label"] == "ORGANIZATION") and not (
                    number != 125 and span["label"] == "RELIGION"
                ):
                    continue
                targets = tuple(
                    sorted(
                        {
                            o.observation_id
                            for o in context.effective
                            if o.page_index == 0
                            and o.entity.start < span["end"]
                            and span["start"] < o.entity.end
                        }
                    )
                )
                edits.append(
                    CorrectionEdit(
                        span=service.bind(
                            host.author_credential, case["id"], 0, span["start"], span["end"]
                        ),
                        category=span["label"],
                        action="mask",
                        rationale="incomplete_boundary" if number == 125 else "span_conflict",
                        target_observation_ids=targets,
                    )
                )
        malicious_rejected = unauthorized_rejected = False
        if edits:
            forged = edits[0].model_copy(
                update={"span": edits[0].span.model_copy(update={"source_span_digest": "f" * 64})}
            )
            try:
                service.propose(host.author_credential, case["id"], (forged,))
            except SafeError as exc:
                malicious_rejected = exc.code == "private_correction_denied"
            try:
                service.propose(host.approver_credential, case["id"], tuple(edits))
            except SafeError as exc:
                unauthorized_rejected = exc.code == "private_correction_denied"
            proposal = service.propose(host.author_credential, case["id"], tuple(edits))
            receipt = service.apply(host.author_credential, proposal.correction_id)
            candidate = host.candidates[receipt.candidate_digest]
            assert host.contexts[case["id"]] == context
        with tempfile.TemporaryDirectory() as directory:
            authority = HostAuthorizationBridge(host).authority(
                b"synthetic-demo-signing-key-not-production",
                SQLiteApprovalLedger(Path(directory) / "approvals.sqlite", initialize=True),
                ("synthetic-private",),
            )
            blocked = False
            try:
                authority.export(
                    candidate, None, actor=host.author_credential, destination="synthetic-private"
                )
            except SafeError as exc:
                blocked = exc.code == "release_not_approved"
        rows.append(
            dict(
                case_id=case["id"],
                original_outcome=original,
                machine_candidate_digest=context.material.candidate_digest,
                machine_observations_digest=commitment(context.machine),
                source_canaries_covered_before=before,
                proposed_edits=[e.model_dump(mode="json") for e in edits],
                new_candidate_produced=bool(edits),
                final_candidate_digest=digest(candidate),
                source_canaries_covered_after=coverage(candidate, case),
                document_exact_match_after=candidate.pages[0].anonymized_text == case["expected"],
                final_sensitivity=candidate.sensitivity,
                release_blocked=blocked,
                completed_release_reviews=len(host.completed),
                malformed_correction_rejected=malicious_rejected if edits else None,
                unauthorized_correction_rejected=unauthorized_rejected if edits else None,
                capture_inference_ms=inference_ms,
            )
        )
    report = dict(
        schema_version="gate03f-synthetic-reviewed-correction-demo/v1",
        source_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        corpus_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        model_revision=model.revision,
        prompts=model.prompts,
        threshold=model.threshold,
        config_digest=context.machine.provenance.configuration_digest,
        actual_inference_calls=len(rows),
        loading_ms=loading_ms,
        peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        runtime_versions={
            k: importlib.metadata.version(k)
            for k in ("gliner", "torch", "transformers", "numpy", "pydantic")
        },
        denied_network_calls=denied,
        cases=rows,
        correction_source="simulated authorized human using existing synthetic gold spans",
        production_identity_qualified=False,
        durable_correction_store_qualified=False,
        independent_qualification=False,
        model_qualification_passed=False,
        corrected_outputs_count_as_model_true_positives=False,
        automated_release_eligible=False,
    )
    write_candidate(output, (json.dumps(report, indent=2) + "\n").encode())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = run(args.cache, args.output)
    except Exception:
        raise SystemExit("private_correction_demo_failed") from None
    # Emit only aggregate results; source text and paths are never printed.
    print(
        json.dumps(
            dict(
                cases=len(report["cases"]),
                manual_corrections=sum(c["new_candidate_produced"] for c in report["cases"]),
                all_seeded_canaries_covered=all(
                    c["source_canaries_covered_after"] for c in report["cases"]
                ),
                all_release_blocked=all(c["release_blocked"] for c in report["cases"]),
            )
        )
    )


if __name__ == "__main__":
    main()
