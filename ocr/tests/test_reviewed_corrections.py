"""Source-bound human edits and actual approval/ledger regressions, synthetic only."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from ocr.anonymization.approval_ledger import SQLiteApprovalLedger
from ocr.anonymization.candidate_io import parse_candidate
from ocr.anonymization.corrections import (
    CorrectionEdit,
    CorrectionService,
    author_digest,
    commitment,
    prepare_review,
)
from ocr.anonymization.engine import AnonymizationEngine
from ocr.anonymization.host_adapter import HostAuthorizationBridge
from ocr.anonymization.private_review import PrivateReviewBridge
from ocr.anonymization.release_policy import digest
from ocr.anonymization.schemas import CorrectedCandidate, Entity
from ocr.schemas import SafeError
from ocr.tests.fixtures.correction_host import CorrectionHostFixture
from ocr.tests.test_anonymization_security import StubRecognizer, evidence

TEXT = "PrivateSyntheticName works at Silver Lantern Archives."


class Recognizer(StubRecognizer):
    def __init__(self, specs):
        self.specs = specs

    def recognize(self, text):
        return [
            Entity(
                start=text.index(word),
                end=text.index(word) + len(word),
                text=word,
                label=label,
                score=0.9,
                source="multilingual_ner",
                model_revision=self.revision,
            )
            for word, label in self.specs
        ]


def host_fixture(
    *,
    specs=(("PrivateSyntheticName", "PERSON"), ("Silver Lantern", "ORGANIZATION")),
    text=TEXT,
    level="D2",
    job="original-doc",
    separate=True,
):
    result = evidence(level, (text,))
    result.job_id = job
    context = prepare_review(
        AnonymizationEngine(Recognizer(specs)),
        result,
        review_ref="original-review",
        environment_id="synthetic-room",
        expires_at=int(time.time()) + 600,
    )
    return CorrectionHostFixture(context, {0: text}, separate_approver=separate)


def edit_for(host, word, category, reason, *, reference="original-review", action="mask", page=0):
    service = CorrectionService(host)
    credential = host.author_credential
    context = service.inspect(credential, reference)
    text = PrivateReviewBridge(host).source_page(credential, reference, page).text
    start = text.index(word)
    end = start + len(word)
    span = service.bind(credential, reference, page, start, end)
    targets = tuple(
        sorted(
            {
                o.observation_id
                for o in context.effective
                if o.page_index == page
                and o.entity.start < end
                and start < o.entity.end
                and (action == "mask" or o.entity.label == category)
            }
        )
    )
    return CorrectionEdit(
        span=span,
        category=category,
        action=action,
        rationale=reason,
        target_observation_ids=targets,
    )


def apply_one(host, edit, *, reference="original-review"):
    service = CorrectionService(host)
    proposal = service.propose(host.author_credential, reference, (edit,))
    receipt = service.apply(host.author_credential, proposal.correction_id)
    return proposal, receipt, host.candidates[receipt.candidate_digest]


@pytest.mark.parametrize("case", ["missing", "boundary", "category", "conflict", "false_positive"])
def test_all_five_supported_corrections(case):
    options = {
        "missing": dict(specs=(), text="PrivateSyntheticName"),
        "boundary": dict(
            specs=(("Silver Lantern", "ORGANIZATION"),), text="Silver Lantern Archives"
        ),
        "category": dict(specs=(("Greystone", "PERSON"),), text="Greystone"),
        "conflict": dict(specs=(("Greystone", "PERSON"), ("Greystone", "CITY")), text="Greystone"),
        "false_positive": dict(specs=(("weather", "PERSON"),), text="weather"),
    }
    host = host_fixture(**options[case])
    old = host.contexts["original-review"]
    snapshot = old.model_dump_json()
    word, category, reason, action = {
        "missing": ("PrivateSyntheticName", "PERSON", "missing_entity", "mask"),
        "boundary": ("Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary", "mask"),
        "category": ("Greystone", "CITY", "wrong_category", "mask"),
        "conflict": ("Greystone", "CITY", "span_conflict", "mask"),
        "false_positive": ("weather", "PERSON", "false_positive", "suppress"),
    }[case]
    proposal, receipt, value = apply_one(
        host, edit_for(host, word, category, reason, action=action)
    )
    assert type(value) is CorrectedCandidate and parse_candidate(value.model_dump_json()) == value
    assert value.pages[0].anonymized_text == (
        "weather" if case == "false_positive" else "[" + category + "]"
    )
    assert (
        not value.anonymous and not value.release_approved and not value.automated_release_eligible
    )
    assert (
        value.release_review_required and "human_correction_requires_review" in value.review_reasons
    )
    assert value.correction.proposal_digest == commitment(proposal)
    assert value.provenance == old.machine.provenance
    assert host.contexts["original-review"].model_dump_json() == snapshot
    assert host.contexts[receipt.review_ref].machine == old.machine
    assert value.correction.prior_candidate_digest == old.material.candidate_digest
    assert value.correction.author_subject_digests == (author_digest("synthetic-author"),)
    assert "PrivateSyntheticName" not in value.model_dump_json()
    assert "PrivateSyntheticName" not in proposal.model_dump_json()
    assert "PrivateSyntheticName" not in repr(host.contexts[receipt.review_ref])
    if case != "false_positive":
        assert all(t.source == "human_correction" and t.score is None for t in value.pages[0].tags)
    if old.material.candidate is not None:
        assert (
            value.candidate_id != old.material.candidate.candidate_id
            and digest(value) != old.material.candidate_digest
        )
        assert old.material.candidate_digest in host.candidates
    else:
        with pytest.raises(SafeError, match="invalid_release_candidate"):
            digest(old.material.candidate)


@pytest.mark.parametrize("operation", ["inspect", "submit", "bind", "inspect_proposal", "apply"])
def test_no_correction_permission_even_with_review_and_release_roles(operation):
    host = host_fixture()
    service = CorrectionService(host)
    edit = edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary")
    proposal = service.propose(host.author_credential, "original-review", (edit,))
    host.context_reads = host.proposal_reads = 0
    credential = host.approver_credential
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        if operation == "inspect":
            service.inspect(credential, "original-review")
        if operation == "submit":
            service.propose(credential, "original-review", (edit,))
        if operation == "bind":
            service.bind(credential, "original-review", 0, 0, 4)
        if operation == "inspect_proposal":
            service.inspect_proposal(credential, proposal.correction_id)
        if operation == "apply":
            service.apply(credential, proposal.correction_id)
    assert host.context_reads == host.proposal_reads == 0


@pytest.mark.parametrize(
    "credential",
    [None, False, {"subject": "synthetic-author", "approved": True}, "synthetic-author"],
)
def test_no_request_supplied_identity_or_approval_flag(credential):
    host = host_fixture()
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        CorrectionService(host).inspect(credential, "original-review")


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_digest", "f" * 64),
        ("context_digest", "f" * 64),
        ("machine_snapshot_digest", "f" * 64),
        ("prior_candidate_digest", "f" * 64),
        ("author_subject", "another-author"),
        ("expires_at", 1),
    ],
)
def test_stale_or_inconsistent_proposal_rejected(field, value):
    host = host_fixture()
    service = CorrectionService(host)
    proposal = service.propose(
        host.author_credential,
        "original-review",
        (edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary"),),
    )
    record = host.proposals[proposal.correction_id]
    host.proposals[proposal.correction_id] = record.model_copy(
        update={"proposal": proposal.model_copy(update={field: value})}
    )
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        service.apply(host.author_credential, proposal.correction_id)
    assert len(host.contexts) == 1


@pytest.mark.parametrize(
    "change", ["start", "end", "digest", "page", "raw_text", "extra_replacement", "wrong_target"]
)
def test_incorrect_original_offsets_and_malformed_edits_are_sanitized(change):
    host = host_fixture()
    edit = edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary")
    data = edit.model_dump()
    if change == "start":
        data["span"]["start"] -= 1
    if change == "end":
        data["span"]["end"] += 1
    if change == "digest":
        data["span"]["source_span_digest"] = "f" * 64
    if change == "page":
        data["span"]["page_index"] = 3
    if change == "raw_text":
        data["span"]["text"] = "PrivateSyntheticName"
    if change == "extra_replacement":
        data["replacement_text"] = "PrivateSyntheticName"
    if change == "wrong_target":
        data["target_observation_ids"] = ("f" * 64,)
    with pytest.raises(SafeError, match="^private_correction_denied$") as error:
        CorrectionService(host).propose(host.author_credential, "original-review", (data,))
    assert "PrivateSyntheticName" not in str(error.value)
    assert not host.proposals and len(host.contexts) == 1


def test_cannot_reuse_span_on_other_document_even_same_text():
    one = host_fixture(job="document-one")
    two = host_fixture(job="document-two")
    edit = edit_for(one, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary")
    assert (
        one.contexts["original-review"].machine.evidence_digest
        != two.contexts["original-review"].machine.evidence_digest
    )
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        CorrectionService(two).propose(two.author_credential, "original-review", (edit,))


@pytest.mark.parametrize(
    "mode",
    [
        "overlapping_edits",
        "unacknowledged_prediction",
        "conflict_not_all_targets",
        "unknown_targets",
    ],
)
def test_conflicting_or_silent_overrides_fail_closed(mode):
    host = host_fixture(specs=(("Greystone", "PERSON"), ("Greystone", "CITY")), text="Greystone")
    edit = edit_for(host, "Greystone", "CITY", "span_conflict")
    edits = (edit,)
    if mode == "overlapping_edits":
        edits = (edit, edit.model_copy(update={"category": "PERSON"}))
    if mode == "unacknowledged_prediction":
        edits = (
            edit.model_copy(update={"target_observation_ids": (), "rationale": "missing_entity"}),
        )
    if mode == "conflict_not_all_targets":
        edits = (
            edit.model_copy(update={"target_observation_ids": edit.target_observation_ids[:1]}),
        )
    if mode == "unknown_targets":
        edits = (edit.model_copy(update={"target_observation_ids": ("f" * 64,)}),)
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        CorrectionService(host).propose(host.author_credential, "original-review", edits)
    assert not host.proposals and not host.candidates


@pytest.mark.parametrize("level", ["D1", "D2"])
def test_approval_cannot_follow_correction_and_old_history_remains_valid(tmp_path, level):
    host = host_fixture(level=level)
    bridge = HostAuthorizationBridge(host)
    ledger = SQLiteApprovalLedger(tmp_path / "ledger.sqlite", initialize=True)
    authority = bridge.authority(b"x" * 32, ledger, ("synthetic-private",))
    old = host.contexts["original-review"].material.candidate
    review_id = host.complete_review(host.author_credential, digest(old))
    oldapproval = authority.approve(
        old, actor=host.author_credential, review_id=review_id, destination="synthetic-private"
    )
    proposal, receipt, corrected = apply_one(
        host, edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary")
    )
    assert corrected.sensitivity == corrected.pages[0].original_sensitivity == level
    for approval in (None, oldapproval):
        with pytest.raises(SafeError, match="release_not_approved"):
            authority.export(
                corrected, approval, actor=host.author_credential, destination="synthetic-private"
            )
    output = json.loads(
        authority.export(
            old, oldapproval, actor=host.author_credential, destination="synthetic-private"
        )
    )
    assert output["candidate_digest"] == digest(old) and not output["transmission_authorized"]
    with pytest.raises(SafeError, match="release_approval_consumed_or_unavailable"):
        authority.export(
            old, oldapproval, actor=host.author_credential, destination="synthetic-private"
        )
    author_review = host.complete_review(host.author_credential, receipt.candidate_digest)
    with pytest.raises(SafeError, match="release_not_approved"):
        authority.approve(
            corrected,
            actor=host.author_credential,
            review_id=author_review,
            destination="synthetic-private",
        )
    final_review = host.complete_review(host.approver_credential, receipt.candidate_digest)
    approval = authority.approve(
        corrected,
        actor=host.approver_credential,
        review_id=final_review,
        destination="synthetic-private",
    )
    released = json.loads(
        authority.export(
            corrected, approval, actor=host.approver_credential, destination="synthetic-private"
        )
    )
    assert (
        released["sensitivity"] == level
        and not released["anonymous"]
        and not released["transmission_authorized"]
    )
    with pytest.raises(SafeError, match="release_approval_consumed_or_unavailable"):
        authority.export(
            corrected, approval, actor=host.approver_credential, destination="synthetic-private"
        )


def test_missing_explicit_corrected_release_policy_denies(tmp_path):
    host = host_fixture()
    _, receipt, value = apply_one(
        host, edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary")
    )
    host.corrected_release_allowed = None
    review_id = host.complete_review(host.approver_credential, receipt.candidate_digest)
    authority = HostAuthorizationBridge(host).authority(
        b"x" * 32,
        SQLiteApprovalLedger(tmp_path / "ledger.sqlite", initialize=True),
        ("synthetic-private",),
    )
    with pytest.raises(SafeError, match="release_not_approved"):
        authority.approve(
            value,
            actor=host.approver_credential,
            review_id=review_id,
            destination="synthetic-private",
        )


@pytest.mark.parametrize(
    "mode",
    ["revoked_proposal", "revoked_view", "expired_session", "acl_at_commit", "missing_storage"],
)
def test_current_permission_and_storage_rechecked_at_publication(mode):
    host = host_fixture()
    service = CorrectionService(host)
    proposal = service.propose(
        host.author_credential,
        "original-review",
        (edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary"),),
    )
    if mode == "revoked_proposal":
        host.proposals[proposal.correction_id] = host.proposals[proposal.correction_id].model_copy(
            update={"revoked": True}
        )
    if mode == "revoked_view":
        host.bindings["original-review"] = host.bindings["original-review"].model_copy(
            update={"revoked": True}
        )
    if mode == "expired_session":
        host.identities[host.author_credential] = host.identities[
            host.author_credential
        ].model_copy(update={"expires_at": 0})
    if mode == "acl_at_commit":
        host.before_publish = lambda: host.allowed.clear()
    if mode == "missing_storage":
        host.publish_correction = lambda *a: False
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        service.apply(host.author_credential, proposal.correction_id)
    assert len(host.contexts) == 1


def test_simultaneous_apply_and_proposal_replay_publish_once():
    host = host_fixture()
    service = CorrectionService(host)
    proposal = service.propose(
        host.author_credential,
        "original-review",
        (edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary"),),
    )
    barrier = Barrier(2)

    def worker():
        barrier.wait()
        try:
            return service.apply(host.author_credential, proposal.correction_id)
        except SafeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert sum(r is not None for r in results) == 1 and len(host.contexts) == 2
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        service.apply(host.author_credential, proposal.correction_id)


def test_second_correction_uses_original_offsets_and_retains_chain():
    host = host_fixture(specs=(), text="FirstSyntheticName meets SecondSyntheticName.")
    first, published, old = apply_one(
        host, edit_for(host, "FirstSyntheticName", "PERSON", "missing_entity")
    )
    second, receipt, new = apply_one(
        host,
        edit_for(
            host, "SecondSyntheticName", "PERSON", "missing_entity", reference=published.review_ref
        ),
        reference=published.review_ref,
    )
    assert new.pages[0].anonymized_text == "[PERSON] meets [PERSON]."
    assert second.edits[0].span.start == len("FirstSyntheticName meets ")
    assert new.correction.prior_candidate_digest == digest(old)
    assert host.contexts[receipt.review_ref].machine == host.contexts["original-review"].machine
    assert len(host.contexts) == 3 and len(host.proposals) == 2
    assert CorrectionService(host).inspect_proposal(
        host.author_credential, first.correction_id
    ).record.published_candidate_digest == digest(old)


def test_stale_parallel_proposal_cannot_discard_a_newer_correction():
    host = host_fixture()
    service = CorrectionService(host)
    edit = edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary")
    first = service.propose(host.author_credential, "original-review", (edit,))
    stale = service.propose(host.author_credential, "original-review", (edit,))
    service.apply(host.author_credential, first.correction_id)
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        service.apply(host.author_credential, stale.correction_id)
    assert len(host.contexts) == 2


def test_lost_publication_reply_retains_immutable_recovery_pointer():
    host = host_fixture()
    service = CorrectionService(host)
    proposal = service.propose(
        host.author_credential,
        "original-review",
        (edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary"),),
    )
    original = host.publish_correction

    def lost(*args):
        original(*args)
        raise OSError("SYNTHETIC_PRIVATE_WRITE_ERROR")

    host.publish_correction = lost
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        service.apply(host.author_credential, proposal.correction_id)
    view = service.inspect_proposal(host.author_credential, proposal.correction_id)
    assert view.record.published_candidate_digest in host.candidates
    assert view.record.published_review_ref in host.contexts
    assert len(host.contexts) == 2


def test_unnecessary_empty_correction_has_no_new_candidate_or_approval():
    host = host_fixture()
    before = len(host.candidates)
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        CorrectionService(host).propose(host.author_credential, "original-review", ())
    assert len(host.candidates) == before and not host.proposals


def test_runtime_failure_is_not_a_correctable_conflict():
    class Broken(Recognizer):
        def recognize(self, text):
            raise RuntimeError("PrivateSyntheticName")

    with pytest.raises(SafeError, match="^correction_capture_failed$"):
        prepare_review(
            AnonymizationEngine(Broken(())),
            evidence("D2", (TEXT,)),
            review_ref="original-review",
            environment_id="synthetic-room",
            expires_at=int(time.time()) + 600,
        )


def test_empty_prediction_warning_survives_manual_correction():
    host = host_fixture(specs=(), text="PrivateSyntheticName")
    _, _, value = apply_one(
        host, edit_for(host, "PrivateSyntheticName", "PERSON", "missing_entity")
    )
    assert "empty_prediction" in value.review_reasons


def test_corrected_policy_revocation_is_checked_again_at_export(tmp_path):
    host = host_fixture()
    _, receipt, value = apply_one(
        host, edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary")
    )
    authority = HostAuthorizationBridge(host).authority(
        b"x" * 32,
        SQLiteApprovalLedger(tmp_path / "ledger.sqlite", initialize=True),
        ("synthetic-private",),
    )
    review = host.complete_review(host.approver_credential, receipt.candidate_digest)
    approval = authority.approve(
        value, actor=host.approver_credential, review_id=review, destination="synthetic-private"
    )
    host.corrected_release_allowed = lambda *args: False
    with pytest.raises(SafeError, match="^release_not_approved$"):
        authority.export(
            value, approval, actor=host.approver_credential, destination="synthetic-private"
        )


@pytest.mark.parametrize("mode", ["classification", "malformed", "source", "observation"])
def test_corrupt_private_records_fail_sanitized(mode, caplog):
    host = host_fixture()
    context = host.contexts["original-review"]
    edit = edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary")
    if mode == "malformed":
        host.load_correction_context = lambda ref: {"PrivateSyntheticName": "secret"}
    if mode == "classification":
        host.contexts["original-review"] = context.model_copy(
            update={
                "material": context.material.model_copy(
                    update={
                        "binding": context.material.binding.model_copy(update={"sensitivity": "D0"})
                    }
                )
            }
        )
    if mode == "source":
        host.source = {k: b"PrivateSyntheticName" for k in host.source}
    if mode == "observation":
        bad = context.machine.observations[0].model_copy(update={"observation_id": "f" * 64})
        host.contexts["original-review"] = context.model_copy(
            update={
                "machine": context.machine.model_copy(
                    update={"observations": (bad,) + context.machine.observations[1:]}
                )
            }
        )
    with pytest.raises(SafeError, match="^private_correction_denied$") as caught:
        service = CorrectionService(host)
        service.propose(host.author_credential, "original-review", (edit,))
    assert "PrivateSyntheticName" not in str(caught.value) + caplog.text
    assert len(host.contexts) == 1 and not host.proposals
