import hashlib
import json

import pytest
from ocr.anonymization.approval_ledger import SQLiteApprovalLedger
from ocr.anonymization.host_adapter import HostAuthorizationBridge
from ocr.anonymization.private_transport import LocalDestination, PrivateFileTransport
from ocr.schemas import SafeError
from ocr.tests.fixtures.isolated_host import IsolatedHostAdapter
from ocr.tests.test_anonymization_security import candidate
from pydantic import ValidationError


def setup(tmp_path):
    value = candidate()
    host = IsolatedHostAdapter(value)
    bridge = HostAuthorizationBridge(host)
    ledger = SQLiteApprovalLedger(tmp_path / "ledger.sqlite", initialize=True)
    authority = bridge.authority(b"z" * 32, ledger, [host.policy.destination_id])
    approval = authority.approve(
        value,
        actor=host.credential,
        review_id="fixture-review",
        destination=host.policy.destination_id,
    )
    return value, host, bridge, authority, approval


@pytest.mark.parametrize(
    "failure",
    [
        "requested_identity",
        "boolean",
        "expired_identity",
        "revoked_review",
        "expired_review",
        "incomplete_review",
        "revoked_session",
        "destination_changed",
        "candidate_missing",
        "acl_revoked",
        "destination_revoked",
    ],
)
def test_real_adapter_contract_fails_closed_without_trusting_request_flags(tmp_path, failure):
    value, host, bridge, authority, approval = setup(tmp_path)
    actor = host.credential
    if failure == "requested_identity":
        actor = "fixture-subject"
    elif failure == "boolean":
        actor = True
    elif failure == "expired_identity":
        host.identity = host.identity.model_copy(update={"expires_at": 0})
    elif failure == "revoked_review":
        host.revoked.add("fixture-review")
    elif failure == "expired_review":
        original = host.load_review
        host.load_review = lambda key: original(key).model_copy(update={"expires_at": 0})
    elif failure == "incomplete_review":
        original = host.load_review
        host.load_review = lambda key: original(key).model_copy(update={"completed": False})
    elif failure == "revoked_session":
        host.credential = object()
    elif failure == "destination_changed":
        host.destinations[host.policy.destination_id] = host.policy.model_copy(
            update={"destination_class": "public"}
        )
    elif failure == "candidate_missing":
        host.candidates = frozenset()
    elif failure == "acl_revoked":
        host.allowed.clear()
    else:
        host.destinations.clear()
    with pytest.raises(SafeError):
        authority.export(value, approval, actor=actor, destination="private-fixture")
    assert not host.production_ready


def test_review_evidence_is_immutable_and_candidate_bound(tmp_path):
    value, host, bridge, authority, approval = setup(tmp_path)
    with pytest.raises(TypeError):
        host.records["new"] = host.records["fixture-review"]
    with pytest.raises(ValidationError):
        host.records["fixture-review"].attestation.candidate_digest = "b" * 64
    with pytest.raises(SafeError):
        authority.approve(
            candidate(),
            actor=host.credential,
            review_id="fixture-review",
            destination="private-fixture",
        )


def test_local_delivery_requires_separate_current_authorization_and_retains_classification(
    tmp_path,
):
    value, host, bridge, authority, approval = setup(tmp_path)
    sink = tmp_path / "sink"
    sink.mkdir(mode=0o700)
    transport = PrivateFileTransport(
        [LocalDestination(host.policy, sink)], authorize_transfer=bridge.transfer_authorized
    )
    exported = authority.export(
        value, approval, actor=host.credential, destination=host.policy.destination_id
    )
    with pytest.raises(SafeError):
        transport.deliver(
            exported, credential=host.credential, destination_id=host.policy.destination_id
        )
    assert not list(sink.iterdir())
    # Explicit privileged fixture registration and separate transfer ACL. Neither
    # happens on export in this library or automatically in a production service.
    artifact = hashlib.sha256(exported.encode()).hexdigest()
    host.register_export(exported)
    host.allowed.add(
        (host.identity.subject, "anonymization:private-transfer", "D2", host.policy.destination_id)
    )
    result = transport.deliver(
        exported, credential=host.credential, destination_id=host.policy.destination_id
    )
    saved = sink / (artifact + ".json")
    assert saved.stat().st_mode & 0o777 == 0o600
    assert json.loads(saved.read_text())["sensitivity"] == "D2"
    assert json.loads(saved.read_text())["transmission_authorized"] is False
    assert result["automated_release_eligible"] is False
    with pytest.raises(SafeError):
        transport.deliver(
            exported, credential=host.credential, destination_id=host.policy.destination_id
        )


@pytest.mark.parametrize(
    "attack",
    [
        "url",
        "redirect",
        "public_fallback",
        "substituted_directory",
        "symlink",
        "extra_raw_metadata",
        "changed_bytes",
    ],
)
def test_transport_prohibits_substitution_fallback_and_unregistered_artifacts(tmp_path, attack):
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
    destination = host.policy.destination_id
    if attack == "url":
        destination = "https://external.invalid/upload"
    elif attack == "public_fallback":
        destination = "public-worker"
    elif attack in ("redirect", "extra_raw_metadata"):
        data = json.loads(exported)
        data["location" if attack == "redirect" else "raw_original"] = "synthetic forbidden field"
        exported = json.dumps(data)
    elif attack == "changed_bytes":
        exported = exported + " "
    else:
        sink.rename(tmp_path / "original")
        if attack == "symlink":
            sink.symlink_to(tmp_path / "original", target_is_directory=True)
        else:
            sink.mkdir(mode=0o700)
    with pytest.raises(SafeError):
        transport.deliver(exported, credential=host.credential, destination_id=destination)
    assert not list(
        (tmp_path / "original" if (tmp_path / "original").exists() else sink).glob("*.json")
    )


@pytest.mark.parametrize(
    "change",
    ["review_revoked", "export_revoked", "export_expired", "review_expired", "acl_revoked"],
)
def test_revocation_after_export_still_blocks_private_delivery(tmp_path, change):
    value, host, bridge, authority, approval = setup(tmp_path)
    sink = tmp_path / "sink"
    sink.mkdir(mode=0o700)
    exported = authority.export(
        value, approval, actor=host.credential, destination=host.policy.destination_id
    )
    artifact = hashlib.sha256(exported.encode()).hexdigest()
    host.register_export(exported)
    host.allowed.add(
        (host.identity.subject, "anonymization:private-transfer", "D2", host.policy.destination_id)
    )
    if change == "review_revoked":
        host.revoked.add("fixture-review")
    elif change == "export_revoked":
        host.exports[artifact] = host.exports[artifact].model_copy(update={"revoked": True})
    elif change == "export_expired":
        host.exports[artifact] = host.exports[artifact].model_copy(update={"expires_at": 0})
    elif change == "review_expired":
        previous = host.load_review
        host.load_review = lambda value: previous(value).model_copy(update={"expires_at": 0})
    else:
        host.allowed.clear()
    transport = PrivateFileTransport(
        [LocalDestination(host.policy, sink)], authorize_transfer=bridge.transfer_authorized
    )
    with pytest.raises(SafeError):
        transport.deliver(
            exported, credential=host.credential, destination_id=host.policy.destination_id
        )
    assert not list(sink.iterdir())
