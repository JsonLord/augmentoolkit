"""Actual approval compatibility and synthetic private-host review security."""

import hashlib
import json
import time
from pathlib import Path

import pytest
from ocr.anonymization.candidate_io import parse_candidate
from ocr.anonymization.engine import AnonymizationEngine
from ocr.anonymization.private_review import (
    PrivateReviewBridge,
    PrivateReviewMaterial,
    ReviewBinding,
    SourcePageReference,
)
from ocr.anonymization.release_policy import digest
from ocr.anonymization.schemas import LABELS, LegacyReleaseCandidate
from ocr.anonymization.status import StatusPolicy
from ocr.schemas import SafeError
from ocr.tests.fixtures.isolated_host import IsolatedHostAdapter
from ocr.tests.test_anonymization_release_policy import PRIVATE, application
from ocr.tests.test_anonymization_security import CANARY, StubRecognizer, candidate, evidence


def test_canonical_provenance_and_no_raw_config_metadata():
    m = StubRecognizer()
    a = AnonymizationEngine(m).handoff(evidence())
    m.prompts = dict(reversed(list(m.prompts.items())))
    b = AnonymizationEngine(m).handoff(evidence())
    assert a.provenance == b.provenance
    m.prompts = m.prompts | {"PERSON": CANARY}
    c = AnonymizationEngine(m).handoff(evidence())
    assert c.provenance != a.provenance
    assert CANARY not in c.model_dump_json()
    assert c.schema_version == "ocr-anonymization-candidate/v2"
    assert not c.anonymous and not c.automated_release_eligible


@pytest.mark.parametrize("change", ["threshold", "terms", "ocr", "placeholders"])
def test_each_policy_change_changes_digest(change):
    m = StubRecognizer()
    original = AnonymizationEngine(m).handoff(evidence()).provenance.configuration_digest
    kwargs = {}
    if change == "threshold":
        m.threshold = 0.4
    if change == "terms":
        kwargs["religion_terms"] = ("Shinto",)
    if change == "ocr":
        kwargs["status_policy"] = StatusPolicy(ocr_enabled=False)
    if change == "placeholders":
        kwargs["placeholders"] = {k: "[HIDDEN_" + k + "]" for k in LABELS}
    assert (
        AnonymizationEngine(m, **kwargs).handoff(evidence()).provenance.configuration_digest
        != original
    )


def test_runtime_configuration_drift_blocks_candidate():
    class Drift(StubRecognizer):
        def recognize(self, text):
            self.threshold = 0.4
            return super().recognize(text)

    with pytest.raises(SafeError, match="recognition_configuration_changed"):
        AnonymizationEngine(Drift()).handoff(evidence())


def test_old_candidate_serialization_and_approval_remain_valid(tmp_path):
    raw = (Path(__file__).parent / "fixtures/candidate_v1.json").read_bytes()
    old = parse_candidate(raw)
    assert type(old) is LegacyReleaseCandidate
    assert old.model_dump_json().encode() == raw
    assert digest(old) == "eb3ab0d101360654f07aa4c614f12873646d44e1d21b1e6cf096033225dec435"
    app = application(tmp_path)
    oldreview = app.review.model_copy(update={"candidate_digest": digest(old)})

    def verify(actor, review_id):
        assert actor == "authenticated-session" and review_id == "review-1"
        return oldreview

    app.verifier.side_effect = verify
    app.value = old
    approval = app.approve()
    new = candidate()
    with pytest.raises(SafeError, match="release_not_approved"):
        app.authority.export(
            new, approval, actor="authenticated-session", destination=PRIVATE.destination_id
        )
    out = json.loads(app.export(approval))
    assert out["candidate_digest"] == digest(old) and out["sensitivity"] == "D2"
    assert not out["transmission_authorized"]


@pytest.mark.parametrize("change", ["unknown_version", "missing_provenance", "model_mismatch"])
def test_unsupported_or_forged_candidate_fails_closed(change):
    data = json.loads(candidate().model_dump_json())
    if change == "unknown_version":
        data["schema_version"] = "ocr-anonymization-candidate/v999"
    if change == "missing_provenance":
        del data["provenance"]
    if change == "model_mismatch":
        data["provenance"]["model_revision"] = "f" * 40
    with pytest.raises(SafeError, match="unsupported_or_invalid_candidate"):
        parse_candidate(json.dumps(data))


def test_modified_provenance_invalidates_new_approval(tmp_path):
    app = application(tmp_path)
    approval = app.approve()
    forged = app.value.model_copy(
        update={
            "provenance": app.value.provenance.model_copy(update={"configuration_digest": "f" * 64})
        }
    )
    with pytest.raises(SafeError, match="release_not_approved"):
        app.authority.export(
            forged, approval, actor="authenticated-session", destination=PRIVATE.destination_id
        )
    assert json.loads(app.export(approval))["candidate_digest"] == digest(app.value)


class SyntheticReviewHost(IsolatedHostAdapter):
    """Isolated test host only; opaque credentials and no production fallback."""

    def __init__(self):
        value = candidate()
        super().__init__(value)
        self.binding = ReviewBinding(
            review_ref="synthetic-review",
            environment_id="private-lab",
            sensitivity="D2",
            expires_at=int(time.time()) + 300,
            revoked=False,
        )
        self.source = CANARY.encode()
        self.material = PrivateReviewMaterial(
            binding=self.binding,
            evidence_digest="a" * 64,
            sources=(
                SourcePageReference(
                    evidence_ref="b" * 64,
                    page_index=0,
                    sha256=hashlib.sha256(self.source).hexdigest(),
                    characters=len(CANARY),
                ),
            ),
            candidate=value,
            candidate_digest=digest(value),
            provenance=value.provenance,
            blocking_reason=None,
        )
        self.allowed.add((self.identity.subject, "anonymization:review", "D2", "private-lab"))
        self.reads = 0

    def review_binding(self, reference):
        if reference != self.binding.review_ref:
            raise PermissionError(CANARY)
        return self.binding

    def private_environment(self, identifier):
        return identifier == "private-lab"

    def load_review_material(self, reference):
        self.reads += 1
        return self.material

    def read_classified_page(self, reference, index):
        assert reference == "b" * 64 and index == 0
        return self.source


def test_private_review_has_classified_source_and_no_approval_power():
    host = SyntheticReviewHost()
    bridge = PrivateReviewBridge(host)
    material = bridge.open(host.credential, host.binding.review_ref)
    assert material.candidate.pages[0].anonymized_text == "[PERSON]"
    assert material.categories == ("PERSON",)
    assert "review_complete_source_pages" in material.review_flags
    assert material.provenance == material.candidate.provenance
    assert not material.automated_release_eligible and not material.anonymous
    assert CANARY not in repr(material)
    original = bridge.source_page(host.credential, host.binding.review_ref, 0)
    assert original.text == CANARY and original.sensitivity == "D2"
    assert CANARY not in repr(original)
    with pytest.raises(SafeError, match="invalid_release_candidate"):
        digest(material)
    assert host.production_ready is False


@pytest.mark.parametrize(
    "mode", ["credentials", "acl", "expired", "revoked", "public_environment", "candidate_missing"]
)
def test_private_review_refuses_missing_authority(mode):
    host = SyntheticReviewHost()
    credential = host.credential
    if mode == "credentials":
        credential = {"subject": host.identity.subject, "approved": True}
    if mode == "acl":
        host.allowed.clear()
    if mode == "expired":
        host.identity = host.identity.model_copy(update={"expires_at": 0})
    if mode == "revoked":
        host.binding = host.binding.model_copy(update={"revoked": True})
    if mode == "public_environment":
        host.private_environment = lambda identifier: False
    if mode == "candidate_missing":
        host.candidates = frozenset()
    with pytest.raises(SafeError, match="^private_review_access_denied$"):
        PrivateReviewBridge(host).open(credential, host.binding.review_ref)
    if mode != "candidate_missing":
        assert host.reads == 0


@pytest.mark.parametrize(
    "mode",
    [
        "changed_source",
        "changed_class",
        "changed_digest",
        "revoked_during_read",
        "session_expired_during_read",
        "url_reference",
    ],
)
def test_private_source_integrity_and_current_authorization(mode):
    host = SyntheticReviewHost()
    bridge = PrivateReviewBridge(host)
    if mode == "changed_source":
        host.source = b"SYNTHETIC_ALTERED"
    if mode == "changed_class":
        host.material = host.material.model_copy(
            update={"binding": host.binding.model_copy(update={"sensitivity": "D0"})}
        )
    if mode == "changed_digest":
        host.material = host.material.model_copy(update={"candidate_digest": "f" * 64})
    if mode == "url_reference":
        host.material = host.material.model_copy(
            update={
                "sources": (
                    host.material.sources[0].model_copy(
                        update={"evidence_ref": "https://example.invalid"}
                    ),
                )
            }
        )
    if mode in ("revoked_during_read", "session_expired_during_read"):
        original = host.read_classified_page

        def changed(reference, index):
            if mode == "revoked_during_read":
                host.binding = host.binding.model_copy(update={"revoked": True})
            else:
                host.identity = host.identity.model_copy(update={"expires_at": 0})
            return original(reference, index)

        host.read_classified_page = changed
    with pytest.raises(SafeError, match="^private_review_access_denied$"):
        bridge.source_page(host.credential, host.binding.review_ref, 0)


def test_blocked_review_record_has_reason_and_original_but_no_candidate():
    host = SyntheticReviewHost()
    host.material = host.material.model_copy(
        update={"candidate": None, "candidate_digest": None, "blocking_reason": "conflicting_spans"}
    )
    material = PrivateReviewBridge(host).open(host.credential, host.binding.review_ref)
    assert material.candidate is None and material.categories == ()
    assert "processing_blocked" in material.review_flags and material.release_review_required
    assert (
        PrivateReviewBridge(host).source_page(host.credential, host.binding.review_ref, 0).text
        == CANARY
    )


def test_legacy_review_cannot_invent_configuration_or_accept_new_normalization():
    host = SyntheticReviewHost()
    old = parse_candidate((Path(__file__).parent / "fixtures/candidate_v1.json").read_bytes())
    host.material = host.material.model_copy(
        update={"candidate": old, "candidate_digest": digest(old), "provenance": None}
    )
    host.candidates = frozenset((digest(old),))
    packet = PrivateReviewBridge(host).open(host.credential, host.binding.review_ref)
    assert "legacy_configuration_unknown" in packet.review_flags and packet.provenance is None
    forged = json.loads(old.model_dump_json())
    forged["pages"][0]["tags"] = [
        dict(
            start=0,
            end=1,
            label="PERSON",
            placeholder="[PERSON]",
            source="deterministic",
            model_revision="unit",
            normalization="status-ocr-v1",
        )
    ]
    with pytest.raises(SafeError, match="unsupported_or_invalid_candidate"):
        parse_candidate(json.dumps(forged))


def test_replacement_version_and_normalization_configuration_are_committed(monkeypatch):
    import importlib
    import re

    module = importlib.import_module("ocr.anonymization.provenance")
    base = AnonymizationEngine(StubRecognizer()).handoff(evidence()).provenance
    monkeypatch.setattr(module, "REPLACEMENT_POLICY_VERSION", "future-policy-version")
    changed = AnonymizationEngine(StubRecognizer()).handoff(evidence()).provenance
    assert changed.configuration_digest != base.configuration_digest
    monkeypatch.setattr(module, "LINE_WRAP", re.compile(r"not-the-current-normalization"))
    assert (
        AnonymizationEngine(StubRecognizer()).handoff(evidence()).provenance.configuration_digest
        != changed.configuration_digest
    )


def test_unknown_recognizer_configuration_is_never_defaulted():
    class Missing(StubRecognizer):
        threshold = None

    with pytest.raises(SafeError, match="invalid_recognition_configuration"):
        AnonymizationEngine(Missing()).handoff(evidence())


def test_revocation_while_loading_review_material_prevents_return():
    host = SyntheticReviewHost()
    original = host.load_review_material

    def changed(reference):
        result = original(reference)
        host.binding = host.binding.model_copy(update={"revoked": True})
        return result

    host.load_review_material = changed
    with pytest.raises(SafeError, match="^private_review_access_denied$"):
        PrivateReviewBridge(host).open(host.credential, host.binding.review_ref)
