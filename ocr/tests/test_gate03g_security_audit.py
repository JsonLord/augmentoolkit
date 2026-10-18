"""Independent adversarial checks of review, release and publication boundaries.

Synthetic identity/review fixtures; real SQLite locking and filesystem operations.
"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from ocr.anonymization import approval_ledger, private_transport
from ocr.anonymization.approval_ledger import SQLiteApprovalLedger
from ocr.anonymization.host_adapter import HostAuthorizationBridge
from ocr.anonymization.private_transport import LocalDestination, PrivateFileTransport
from ocr.anonymization.release_policy import ReleaseAuthority
from ocr.schemas import SafeError
from ocr.tests.test_anonymization_release_policy import PRIVATE, application
from ocr.tests.test_private_host_adapters import setup
from ocr.tests.test_reviewed_corrections import apply_one, edit_for, host_fixture


def test_corrected_completed_review_must_bind_original_ocr_evidence(tmp_path):
    host = host_fixture()
    _, receipt, candidate = apply_one(
        host, edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary")
    )
    review_id = host.complete_review(host.approver_credential, receipt.candidate_digest)
    record = host.completed[review_id]
    host.completed[review_id] = record.model_copy(
        update={"attestation": record.attestation.model_copy(update={"evidence_digest": "f" * 64})}
    )
    authority = HostAuthorizationBridge(host).authority(
        b"x" * 32,
        SQLiteApprovalLedger(tmp_path / "ledger.sqlite", initialize=True),
        ("synthetic-private",),
    )
    with pytest.raises(SafeError, match="^release_not_approved$"):
        authority.approve(
            candidate,
            actor=host.approver_credential,
            review_id=review_id,
            destination="synthetic-private",
        )


def test_expiry_during_real_sqlite_lock_wait_does_not_consume(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite"
    ledger = SQLiteApprovalLedger(path, initialize=True)
    waiting = Event()
    clock = [1000]
    monkeypatch.setattr(approval_ledger, "time", SimpleNamespace(time=lambda: clock[0]))

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "BEGIN IMMEDIATE":
                waiting.set()
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr(
        ledger, "_connect", lambda: sqlite3.connect(path, timeout=5, factory=Connection)
    )
    blocking = sqlite3.connect(path)
    blocking.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ledger.consume, "expires-under-contention", 1001)
            try:
                assert waiting.wait(2)
                clock[0] = 1001
            finally:
                blocking.rollback()
            assert future.result(timeout=5) is False
    finally:
        blocking.close()
    with sqlite3.connect(path) as check:
        assert check.execute("SELECT id FROM consumed").fetchall() == []


def test_private_delivery_revocation_during_write_prevents_publication(tmp_path, monkeypatch):
    value, host, bridge, authority, approval = setup(tmp_path)
    sink = tmp_path / "sink"
    sink.mkdir(mode=0o700)
    transport = PrivateFileTransport(
        [LocalDestination(host.policy, sink)], authorize_transfer=bridge.transfer_authorized
    )
    exported = authority.export(
        value, approval, actor=host.credential, destination=host.policy.destination_id
    )
    host.register_export(exported)
    host.allowed.add(
        (host.identity.subject, "anonymization:private-transfer", "D2", host.policy.destination_id)
    )
    original_fsync = private_transport.os.fsync

    def revoke_after_write(fd):
        original_fsync(fd)
        host.allowed.clear()

    monkeypatch.setattr(private_transport.os, "fsync", revoke_after_write)
    with pytest.raises(SafeError, match="^private_transfer_denied_or_failed$"):
        transport.deliver(
            exported, credential=host.credential, destination_id=host.policy.destination_id
        )
    assert list(sink.iterdir()) == []


def test_uncorrected_release_requires_explicit_operator_policy(tmp_path):
    app = application(tmp_path)
    unconfigured = ReleaseAuthority(
        b"x" * 32,
        app.authorizer,
        verify_review=app.verifier,
        consume_approval=app.ledger.consume,
        destinations=(PRIVATE,),
    )
    with pytest.raises(SafeError, match="^release_not_approved$"):
        unconfigured.approve(
            app.value,
            actor="authenticated-session",
            review_id="review-1",
            destination=PRIVATE.destination_id,
        )


def test_transfer_rejects_mismatched_completed_review_lookup(tmp_path):
    value, host, bridge, authority, approval = setup(tmp_path)
    exported = authority.export(
        value, approval, actor=host.credential, destination=host.policy.destination_id
    )
    host.register_export(exported)
    artifact = next(iter(host.exports))
    host.allowed.add(
        (host.identity.subject, "anonymization:private-transfer", "D2", host.policy.destination_id)
    )
    original = host.load_review
    host.load_review = lambda key: original(key).model_copy(
        update={
            "attestation": original(key).attestation.model_copy(
                update={"review_id": "different-review"}
            )
        }
    )
    assert (
        bridge.transfer_authorized(host.credential, artifact, "D2", host.policy.destination_id)
        is False
    )


@pytest.mark.parametrize("change", ["acl", "legacy_policy"])
def test_revocation_during_consumption_burns_approval_without_returning_output(tmp_path, change):
    app = application(tmp_path)
    approval = app.approve()
    consume = app.authority._consume

    def consume_then_revoke(identifier, expiry):
        consumed = consume(identifier, expiry)
        if change == "acl":
            app.authorizer.side_effect = None
            app.authorizer.return_value = False
        else:
            app.authority._legacy_policy = None
        return consumed

    app.authority._consume = consume_then_revoke
    with pytest.raises(SafeError, match="^release_not_approved$"):
        app.export(approval)
    # Fail-closed final authorization burns the original nonce; no retry release.
    assert not app.ledger.consume(approval.approval_id, approval.expires_at)


@pytest.mark.parametrize("policy", [None, False, 1, "allow", RuntimeError("private-canary")])
@pytest.mark.parametrize("phase", ["approve", "export"])
def test_legacy_policy_is_explicit_literal_true_and_current(tmp_path, policy, phase):
    app = application(tmp_path)
    approval = app.approve() if phase == "export" else None

    def decision(candidate, review):
        if isinstance(policy, Exception):
            raise policy
        return policy

    app.authority._legacy_policy = None if policy is None else decision
    with pytest.raises(SafeError, match="^release_not_approved$") as caught:
        app.export(approval) if phase == "export" else app.approve()
    assert "private-canary" not in str(caught.value)


def test_missing_host_legacy_policy_denies_existing_approval(tmp_path):
    value, host, bridge, authority, approval = setup(tmp_path)
    host.legacy_release_allowed = None
    with pytest.raises(SafeError, match="^release_not_approved$"):
        authority.export(
            value, approval, actor=host.credential, destination=host.policy.destination_id
        )


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_version_coercion_does_not_bypass_corrected_default_deny(tmp_path, version):
    from ocr.anonymization.candidate_io import parse_candidate
    from ocr.anonymization.release_policy import digest
    from ocr.anonymization.schemas import LegacyReleaseCandidate, ReleaseCandidate

    host = host_fixture(specs=(("weather", "PERSON"),), text="weather")
    _, receipt, corrected = apply_one(
        host, edit_for(host, "weather", "PERSON", "false_positive", action="suppress")
    )
    review_id = host.complete_review(host.approver_credential, receipt.candidate_digest)
    bridge = HostAuthorizationBridge(host)
    authority = ReleaseAuthority(
        b"x" * 32,
        bridge.acl,
        verify_review=bridge.review,
        consume_approval=SQLiteApprovalLedger(tmp_path / "ledger.sqlite", initialize=True).consume,
        destinations=(host.policy,),
        legacy_policy=lambda *args: True,
    )
    # Even explicit v1/v2 permission is not a fallback correction permission.
    with pytest.raises(SafeError, match="^release_not_approved$"):
        authority.approve(
            corrected,
            actor=host.approver_credential,
            review_id=review_id,
            destination=host.policy.destination_id,
        )
    parent = LegacyReleaseCandidate if version == "v1" else ReleaseCandidate
    with pytest.raises(ValueError):
        parent.model_validate(corrected)
    malformed = corrected.model_copy(
        update={"schema_version": "ocr-anonymization-candidate/" + version}
    )
    with pytest.raises(SafeError, match="^invalid_release_candidate$"):
        digest(malformed)
    with pytest.raises(SafeError, match="^unsupported_or_invalid_candidate$"):
        parse_candidate(malformed.model_dump_json())


def test_post_link_sync_failure_leaves_only_complete_private_artifact(tmp_path, monkeypatch):
    import hashlib
    import json
    import stat

    value, host, bridge, authority, approval = setup(tmp_path)
    exported = authority.export(
        value, approval, actor=host.credential, destination=host.policy.destination_id
    )
    host.register_export(exported)
    host.allowed.add(
        (host.identity.subject, "anonymization:private-transfer", "D2", host.policy.destination_id)
    )
    sink = tmp_path / "sink"
    sink.mkdir(mode=0o700)
    transport = PrivateFileTransport(
        [LocalDestination(host.policy, sink)], authorize_transfer=bridge.transfer_authorized
    )
    original = private_transport.os.fsync

    def fail_directory_sync(fd):
        if stat.S_ISDIR(private_transport.os.fstat(fd).st_mode):
            raise OSError("private-canary")
        original(fd)

    monkeypatch.setattr(private_transport.os, "fsync", fail_directory_sync)
    with pytest.raises(SafeError, match="^private_transfer_denied_or_failed$"):
        transport.deliver(
            exported, credential=host.credential, destination_id=host.policy.destination_id
        )
    files = list(sink.iterdir())
    assert (
        len(files) == 1 and files[0].name == hashlib.sha256(exported.encode()).hexdigest() + ".json"
    )
    assert files[0].read_bytes() == exported.encode()
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert json.loads(files[0].read_bytes())["sensitivity"] == "D2"
