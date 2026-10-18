"""Synthetic identity/review/ACL callbacks are test fixtures, never production defaults."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from unittest.mock import Mock

import pytest
from ocr.anonymization import ReleaseAuthority
from ocr.anonymization.approval_ledger import SQLiteApprovalLedger
from ocr.anonymization.release_policy import digest
from ocr.anonymization.schemas import Destination, VerifiedReview
from ocr.handoff import anonymization_handoff
from ocr.schemas import SafeError
from ocr.tests.test_anonymization_security import CANARY, candidate, evidence

PRIVATE = Destination(
    destination_id="private-archive",
    destination_class="private",
    allowed_sensitivities=("D0", "D1", "D2"),
)
OTHER = Destination(
    destination_id="private-other",
    destination_class="private",
    allowed_sensitivities=("D0", "D1", "D2"),
)
PUBLIC = Destination(
    destination_id="public-download",
    destination_class="public",
    allowed_sensitivities=("D0", "D1", "D2"),
)


@dataclass
class ApplicationFixture:
    value: object
    review: VerifiedReview
    authority: ReleaseAuthority
    ledger: SQLiteApprovalLedger
    authorizer: object
    verifier: object

    def approve(self, **changes):
        return self.authority.approve(
            self.value,
            **(
                dict(
                    actor="authenticated-session",
                    review_id="review-1",
                    destination=PRIVATE.destination_id,
                )
                | changes
            ),
        )

    def export(self, approval=None, **changes):
        return self.authority.export(
            self.value,
            approval,
            **(dict(actor="authenticated-session", destination=PRIVATE.destination_id) | changes),
        )


def application(tmp_path, *, level="D2"):
    value = candidate(level)
    review = VerifiedReview(
        reviewer_subject="opaque-subject-1",
        review_id="review-1",
        evidence_digest="a" * 64,
        candidate_digest=digest(value),
        sensitivity=level,
        destination_id=PRIVATE.destination_id,
        completed_at=int(time.time()),
    )

    def verify(actor, review_id):
        if actor != "authenticated-session" or review_id != "review-1":
            raise ValueError(CANARY)
        return review

    verifier = Mock(side_effect=verify)
    authorizer = Mock(
        side_effect=lambda r, scope, dest: (
            r.reviewer_subject == "opaque-subject-1"
            and scope == "anonymization:release"
            and dest.destination_id == PRIVATE.destination_id
        )
    )
    ledger = SQLiteApprovalLedger(tmp_path / "approvals.sqlite", initialize=True)
    authority = ReleaseAuthority(
        b"x" * 32,
        authorizer,
        verify_review=verifier,
        consume_approval=ledger.consume,
        destinations=(PRIVATE, OTHER, PUBLIC),
    )
    return ApplicationFixture(value, review, authority, ledger, authorizer, verifier)


@pytest.mark.parametrize("level", ["D0", "D1", "D2"])
def test_reviewed_export_is_destination_bound_classified_and_single_use(tmp_path, level):
    app = application(tmp_path, level=level)
    assert app.value.automated_release_eligible is False
    with pytest.raises(SafeError, match="release_not_approved"):
        app.export()
    approval = app.approve()
    output = json.loads(app.export(approval))
    assert output["sensitivity"] == level and output["anonymous"] is False
    assert (
        output["destination_id"] == PRIVATE.destination_id
        and output["destination_class"] == "private"
    )
    assert output["transmission_authorized"] is False
    assert CANARY not in json.dumps(output)
    assert "reviewer_subject" not in output and "evidence_digest" not in output
    with pytest.raises(SafeError, match="release_approval_consumed_or_unavailable"):
        app.export(approval)


@pytest.mark.parametrize(
    "changed",
    [
        dict(actor="forged-session"),
        dict(review_id="missing-review"),
        dict(destination="https://attacker.invalid"),
        dict(destination=PUBLIC.destination_id),
        dict(destination=OTHER.destination_id),
        dict(format="markdown"),
        dict(format="text"),
    ],
)
def test_issue_requires_authenticated_review_and_approved_private_destination(tmp_path, changed):
    app = application(tmp_path)
    with pytest.raises(SafeError):
        app.approve(**changed)


@pytest.mark.parametrize(
    "changed",
    [
        dict(actor="forged-session"),
        dict(destination=OTHER.destination_id),
        dict(destination=PUBLIC.destination_id),
        dict(destination="arbitrary"),
        dict(format="text"),
        dict(format="markdown"),
    ],
)
def test_export_cannot_change_identity_destination_or_format(tmp_path, changed):
    app = application(tmp_path)
    approval = app.approve()
    with pytest.raises(SafeError):
        app.export(approval, **changed)
    # A rejected operation must not burn a still valid approval.
    assert json.loads(app.export(approval))["release_approved"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewer_subject", "another-subject"),
        ("candidate_digest", "b" * 64),
        ("sensitivity", "D0"),
        ("destination_id", OTHER.destination_id),
        ("completed_at", 0),
        ("completed_at", 9_999_999_999),
        ("review_id", "wrong"),
    ],
)
def test_incomplete_stale_or_mismatched_review_evidence_rejected(tmp_path, field, value):
    app = application(tmp_path)
    app.verifier.side_effect = None
    app.verifier.return_value = app.review.model_copy(update={field: value})
    with pytest.raises(SafeError, match="release_not_approved"):
        app.approve()


def test_boolean_review_claims_are_not_verified_evidence(tmp_path):
    app = application(tmp_path)
    app.verifier.side_effect = None
    app.verifier.return_value = {"review_completed": True}
    with pytest.raises(SafeError, match="release_not_approved"):
        app.approve()


@pytest.mark.parametrize(
    "field,value",
    [
        ("signature", "0" * 64),
        ("expires_at", 0),
        ("issued_at", 9_999_999_999),
        ("candidate_digest", "0" * 64),
        ("approval_id", "1" * 32),
        ("format", "text"),
        ("destination", OTHER),
    ],
)
def test_approval_tampering_or_expiration_rejected(tmp_path, field, value):
    app = application(tmp_path)
    approval = app.approve()
    with pytest.raises(SafeError, match="release_not_approved"):
        app.export(approval.model_copy(update={field: value}))


def test_expired_approval_and_acl_revocation_rechecked_at_export(tmp_path, monkeypatch):
    app = application(tmp_path)
    approval = app.approve()
    app.authorizer.side_effect = None
    app.authorizer.return_value = False
    with pytest.raises(SafeError, match="release_not_approved"):
        app.export(approval)
    app.authorizer.return_value = True
    monkeypatch.setattr("ocr.anonymization.release_policy.time.time", lambda: approval.expires_at)
    with pytest.raises(SafeError, match="release_not_approved"):
        app.export(approval)


def test_review_record_changes_invalidate_previous_approval(tmp_path):
    app = application(tmp_path)
    approval = app.approve()
    app.verifier.side_effect = None
    app.verifier.return_value = app.review.model_copy(update={"evidence_digest": "b" * 64})
    with pytest.raises(SafeError, match="release_not_approved"):
        app.export(approval)


def test_candidate_changes_and_forged_copies_rejected(tmp_path):
    app = application(tmp_path)
    approval = app.approve()
    with pytest.raises(SafeError, match="release_not_approved"):
        app.authority.export(
            candidate(), approval, actor="authenticated-session", destination=PRIVATE.destination_id
        )
    with pytest.raises(SafeError, match="invalid_release_candidate"):
        app.authority.approve(
            app.value.model_copy(update={"sensitivity": "D0"}),
            actor="authenticated-session",
            review_id="review-1",
            destination=PRIVATE.destination_id,
        )


def test_replay_ledger_persists_across_authority_instances_and_races(tmp_path):
    app = application(tmp_path)
    approval = app.approve()
    restarted_ledger = SQLiteApprovalLedger(tmp_path / "approvals.sqlite")
    restarted = ReleaseAuthority(
        b"x" * 32,
        app.authorizer,
        verify_review=app.verifier,
        consume_approval=restarted_ledger.consume,
        destinations=(PRIVATE,),
    )

    def attempt(authority):
        try:
            authority.export(
                app.value,
                approval,
                actor="authenticated-session",
                destination=PRIVATE.destination_id,
            )
            return True
        except SafeError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, [app.authority, restarted])) == 1
    assert not attempt(restarted)
    assert (tmp_path / "approvals.sqlite").stat().st_mode & 0o777 == 0o600


def test_ledger_unavailability_never_returns_export(tmp_path):
    app = application(tmp_path)
    approval = app.approve()
    app.authority._consume = Mock(side_effect=RuntimeError(CANARY))
    with pytest.raises(SafeError, match="release_approval_consumed_or_unavailable") as exc:
        app.export(approval)
    assert CANARY not in str(exc.value)


def test_sensitive_public_destination_is_denied_even_by_permissive_application(tmp_path):
    app = application(tmp_path)
    app.authorizer.side_effect = None
    app.authorizer.return_value = True  # Deliberately malicious policy in this regression only.
    with pytest.raises(SafeError, match="release_destination_not_approved"):
        app.approve(destination=PUBLIC.destination_id)


def test_d0_text_requires_separately_scoped_approval(tmp_path):
    app = application(tmp_path, level="D0")
    approval = app.approve(format="text")
    with pytest.raises(SafeError, match="release_not_approved"):
        app.export(approval, format="json")
    assert app.export(approval, format="text") == "[PERSON]"


def test_legacy_dictionary_cannot_receive_approval(tmp_path):
    app = application(tmp_path)
    legacy = anonymization_handoff(evidence(), lambda text: {"text": text, "tags": []})
    with pytest.raises(SafeError, match="invalid_release_candidate"):
        app.authority.approve(
            legacy,
            actor="authenticated-session",
            review_id="review-1",
            destination=PRIVATE.destination_id,
        )


def test_authority_has_no_default_identity_review_or_replay_authorization():
    with pytest.raises(TypeError):
        ReleaseAuthority(b"x" * 32, lambda *args: True)


def test_legacy_boolean_review_or_v1_approval_cannot_bypass_new_boundary(tmp_path):
    app = application(tmp_path)
    with pytest.raises(TypeError):
        app.authority.approve(app.value, actor="authenticated-session", review_completed=True)
    with pytest.raises(SafeError, match="release_not_approved"):
        app.export(
            {
                "candidate_digest": digest(app.value),
                "expires_at": int(time.time()) + 900,
                "signature": "a" * 64,
            }
        )


def test_replay_ledger_rejects_shared_permissions_or_symlink(tmp_path):
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"")
    bad.chmod(0o644)
    with pytest.raises(SafeError, match="approval_ledger_unavailable"):
        SQLiteApprovalLedger(bad)
    link = tmp_path / "link.sqlite"
    link.symlink_to(bad)
    with pytest.raises(SafeError, match="approval_ledger_unavailable"):
        SQLiteApprovalLedger(link)


def test_signed_approval_json_roundtrip_preserves_identity_and_destination_binding(tmp_path):
    from ocr.anonymization.schemas import Approval

    app = application(tmp_path)
    original = app.approve()
    restored = Approval.model_validate_json(original.model_dump_json())
    assert json.loads(app.export(restored))["destination_id"] == PRIVATE.destination_id
    with pytest.raises(SafeError, match="release_approval_consumed_or_unavailable"):
        app.export(original)
