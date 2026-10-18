import fcntl
import json
import multiprocessing
import os
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor

import pytest
from ocr.anonymization.approval_ledger import SQLiteApprovalLedger, validate_key_topology
from ocr.anonymization.recovery import recover_staging
from ocr.schemas import SafeError
from ocr.tests.test_anonymization_release_policy import application


def consume_worker(args):
    path, identity, nonce, expires = args
    return SQLiteApprovalLedger(path, expected_id=identity).consume(nonce, expires)


def open_transaction(path, ready):
    con = sqlite3.connect(path)
    con.execute("BEGIN IMMEDIATE")
    con.execute("INSERT INTO consumed VALUES (?,?)", ("uncommitted", int(time.time()) + 300))
    ready.set()
    time.sleep(60)


def test_persistent_multiprocess_race_restart_and_rotation(tmp_path):
    path = tmp_path / "ledger.sqlite"
    ledger = SQLiteApprovalLedger(path, initialize=True)
    args = (str(path), ledger.ledger_id, "shared-nonce", int(time.time()) + 300)
    with ProcessPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(consume_worker, [args] * 8)) == 1
    assert consume_worker(args) is False
    assert not ledger.consume("expired", int(time.time()) - 1)
    # Actual authority key rotation: old signatures are rejected with new key,
    # while the durable replay ledger is retained (no database reset).
    from ocr.anonymization import ReleaseAuthority
    from ocr.tests.test_anonymization_release_policy import PRIVATE

    app = application(tmp_path)
    approval = app.approve()
    rotated = ReleaseAuthority(
        b"n" * 32,
        app.authorizer,
        verify_review=app.verifier,
        consume_approval=app.ledger.consume,
        destinations=(PRIVATE,),
    )
    with pytest.raises(SafeError):
        rotated.export(
            app.value, approval, actor="authenticated-session", destination=PRIVATE.destination_id
        )

    fresh = rotated.approve(
        app.value,
        actor="authenticated-session",
        review_id="review-1",
        destination=PRIVATE.destination_id,
    )
    assert (
        json.loads(
            rotated.export(
                app.value, fresh, actor="authenticated-session", destination=PRIVATE.destination_id
            )
        )["release_approved"]
        is True
    )


def test_termination_rolls_back_and_committed_consumption_survives(tmp_path):
    path = tmp_path / "ledger.sqlite"
    ledger = SQLiteApprovalLedger(path, initialize=True)
    assert ledger.consume("committed", int(time.time()) + 300)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    process = ctx.Process(target=open_transaction, args=(str(path), ready))
    process.start()
    try:
        assert ready.wait(10)
    finally:
        process.kill()
        process.join(10)
    restarted = SQLiteApprovalLedger(path, expected_id=ledger.ledger_id)
    assert not restarted.consume("committed", int(time.time()) + 300)
    assert restarted.consume("uncommitted", int(time.time()) + 300)


@pytest.mark.parametrize(
    "failure", ["missing", "corrupt", "permissions", "replacement", "identity_mismatch"]
)
def test_storage_failure_never_reinitializes_or_releases(tmp_path, failure):
    path = tmp_path / "ledger.sqlite"
    ledger = SQLiteApprovalLedger(path, initialize=True)
    if failure == "missing":
        path.unlink()
    elif failure == "corrupt":
        path.write_bytes(b"not a database")
    elif failure == "permissions":
        path.chmod(0o644)
    elif failure == "replacement":
        path.rename(tmp_path / "old.sqlite")
        SQLiteApprovalLedger(path, initialize=True)
    if failure == "identity_mismatch":
        with pytest.raises(SafeError):
            SQLiteApprovalLedger(path, expected_id="wrong")
    else:
        with pytest.raises(SafeError):
            ledger.consume("x", int(time.time()) + 300)
        if failure != "replacement":
            with pytest.raises(SafeError):
                SQLiteApprovalLedger(path)
    if failure == "missing":
        assert not path.exists()


def test_multi_host_keys_cannot_share_independent_local_ledgers():
    a = dict(
        host_id="host-a", key_id="key-a", ledger_id="a", backend="sqlite", shared_durable=False
    )
    b = a | dict(host_id="host-b", key_id="key-b", ledger_id="b")
    # Different labels for identical raw keys must not bypass the guard.
    with pytest.raises(SafeError):
        validate_key_topology([a, b], signing_keys={"key-a": b"x" * 32, "key-b": b"x" * 32})
    validate_key_topology([a, b], signing_keys={"key-a": b"x" * 32, "key-b": b"y" * 32})
    # An operator boolean claiming shared durability is not a real shared backend.
    with pytest.raises(SafeError):
        validate_key_topology(
            [
                a | dict(backend="shared", shared_durable=True),
                b | dict(backend="shared", shared_durable=True),
            ],
            signing_keys={"key-a": b"x" * 32, "key-b": b"x" * 32},
        )


def test_classified_cleanup_retains_evidence_and_respects_active_writer_lock(tmp_path):
    old = tmp_path / ".anonymization-abandoned123"
    old.write_bytes(b"classified synthetic staging")
    old.chmod(0o600)
    os.utime(old, (0, 0))
    source = tmp_path / "original-evidence.json"
    source.write_bytes(b"preserve classified evidence")
    linked = tmp_path / ".anonymization-symlink123"
    linked.symlink_to(source)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        with pytest.raises(SafeError):
            recover_staging(tmp_path, minimum_age_seconds=60)
    finally:
        os.close(fd)
    result = recover_staging(tmp_path, minimum_age_seconds=60)
    assert result["removed_staging_files"] == 1 and result["secure_erasure_guaranteed"] is False
    assert source.exists() and linked.is_symlink()


def leave_partial_staging(directory, ready):
    import tempfile

    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(descriptor, fcntl.LOCK_SH)
    fd, name = tempfile.mkstemp(prefix=".anonymization-", dir=directory)
    os.write(fd, b"partial synthetic classified output")
    os.utime(name, (0, 0))
    ready.set()
    time.sleep(30)


def test_killed_writer_recovery_removes_only_private_abandoned_stage(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=leave_partial_staging, args=(str(tmp_path), ready))
    process.start()
    try:
        assert ready.wait(10)
        assert not (tmp_path / "candidate.json").exists()
    finally:
        process.kill()
        process.join(10)
    result = recover_staging(tmp_path, minimum_age_seconds=60)
    assert result["removed_staging_files"] == 1
    assert not list(tmp_path.iterdir())
