"""Structural remediation only; synthetic fixtures, no model or new holdout."""

import hashlib
import json
import runpy
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from ocr.anonymization.approval_ledger import SQLiteApprovalLedger
from ocr.anonymization.candidate_io import parse_candidate
from ocr.anonymization.corrections import CorrectionService
from ocr.anonymization.private_review import PrivateReviewBridge, PrivateReviewMaterial
from ocr.anonymization.release_policy import ReleaseAuthority, digest
from ocr.anonymization.schemas import Destination, VerifiedReview
from ocr.schemas import SafeError
from ocr.tests.test_reviewed_corrections import edit_for, host_fixture

FIXTURES = Path(__file__).parent / "fixtures"
COMPAT = json.loads((FIXTURES / "gate03h_valid_candidates.json").read_bytes())
VERSIONS = ("v1", "v2", "v3")


def valid(version, first=0):
    return parse_candidate(COMPAT["cases"][f"{version}-{first}"]["candidate_json"])


def material_for(candidate, version, first=0):
    original = PrivateReviewMaterial.model_validate_json(
        COMPAT["cases"][f"{version}-{first}"]["material_json"]
    )
    return original.model_copy(
        update={"candidate": candidate, "candidate_digest": digest(candidate)}
    )


@pytest.mark.parametrize(
    "name",
    [
        "test_duplicate_page_manifest_should_be_rejected",
        "test_invalid_replacement_bounds_should_be_rejected_by_parser",
    ],
)
def test_original_review_poc_rejection(name):
    # Preserve and execute the exact original functions. The original duplicate
    # PoC parsed before its raises block; stronger schema rejection now happens
    # earlier. Only that precise sanitized early rejection is accepted here.
    path = FIXTURES / "gate03h_review_pocs.py.txt"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "de869531a2276c99da4cabbd23ab2bf19c09a1e2116a759d1f70f8fb677b3b2c"
    )
    try:
        runpy.run_path(str(path))[name]()
    except SafeError as exc:
        assert name == "test_duplicate_page_manifest_should_be_rejected"
        assert exc.code == "unsupported_or_invalid_candidate"


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("indices", [(0, 0), (1, 1), ()])
def test_schema_rejects_duplicate_and_empty_pages_all_entry_paths(version, indices):
    candidate = valid(version)
    pages = tuple(candidate.pages[i] for i in indices)
    values = candidate.model_dump() | {"pages": pages}
    with pytest.raises(ValueError):
        type(candidate)(**values)
    forged = candidate.model_copy(update={"pages": pages})
    with pytest.raises(ValueError):
        type(candidate).model_validate(forged)
    with pytest.raises(SafeError, match="^unsupported_or_invalid_candidate$"):
        parse_candidate(forged.model_dump_json())
    with pytest.raises(SafeError, match="^invalid_release_candidate$"):
        digest(forged)


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("indices", [(0, 2), (0,), (0, 1, 2), (9, 10)])
def test_private_review_requires_exact_original_page_set(version, indices):
    candidate = valid(version)
    pages = tuple(candidate.pages[min(i, 1)].model_copy(update={"page_index": i}) for i in indices)
    # Unique positive indices are allowed structurally; only immutable source
    # references establish whether a source page is absent or unexpected.
    changed = type(candidate).model_validate(candidate.model_copy(update={"pages": pages}))
    material = material_for(changed, version)
    with pytest.raises(ValueError):
        PrivateReviewMaterial.model_validate(material)
    with pytest.raises(ValueError):
        PrivateReviewMaterial.model_validate_json(material.model_dump_json())


BAD_BOUNDS = [
    (-1, 2),
    (0, -1),
    (0, 0),
    (1, 1),
    (2, 1),
    ("0", 2),
    (0, "2"),
    (False, 2),
    (0, True),
    (0.0, 2),
    (0, 2.0),
    (None, 2),
    (0, None),
]


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("start,end", BAD_BOUNDS)
def test_strict_replacement_bounds_direct_json_and_digest(version, start, end):
    candidate = valid(version)
    page, tag = candidate.pages[0], candidate.pages[0].tags[0]
    with pytest.raises(ValueError):
        type(tag)(**(tag.model_dump() | {"start": start, "end": end}))
    badtag = tag.model_copy(update={"start": start, "end": end})
    badpage = page.model_copy(update={"tags": (badtag,) + page.tags[1:]})
    forged = candidate.model_copy(update={"pages": (badpage,) + candidate.pages[1:]})
    with pytest.raises(ValueError):
        type(candidate).model_validate(forged)
    with pytest.raises(ValueError):
        type(candidate)(**(candidate.model_dump() | {"pages": forged.pages}))
    # Preserve the malformed JSON value's type directly. Serializing an
    # intentionally invalid model_copy may coerce bools into integer output.
    payload = json.loads(candidate.model_dump_json())
    payload["pages"][0]["tags"][0].update(start=start, end=end)
    with pytest.raises(SafeError, match="^unsupported_or_invalid_candidate$"):
        parse_candidate(json.dumps(payload))
    with pytest.raises(SafeError, match="^invalid_release_candidate$"):
        digest(forged)


@pytest.mark.parametrize("version", VERSIONS)
def test_source_aware_upper_bound_and_original_coordinate_semantics(version):
    candidate = valid(version)
    original = material_for(candidate, version)
    # Real valid tags extend beyond the shortened output, using Unicode
    # codepoints rather than UTF-8 bytes or placeholder offsets.
    for page, source in zip(candidate.pages, original.sources, strict=True):
        assert page.tags[0].end == source.characters > len(page.anonymized_text)
        text = COMPAT["text"][page.page_index]
        assert source.characters == len(text) < len(text.encode())
    assert PrivateReviewMaterial.model_validate(original) == original
    page = candidate.pages[0]
    tag = page.tags[0].model_copy(update={"end": original.sources[0].characters + 1})
    forged = candidate.model_copy(
        update={"pages": (page.model_copy(update={"tags": (tag,)}),) + candidate.pages[1:]}
    )
    # No source length is serialized in candidates, so the upper bound belongs
    # at the source-aware boundary. Structural/digest validity is not approval.
    parsed = parse_candidate(forged.model_dump_json())
    with pytest.raises(ValueError):
        PrivateReviewMaterial.model_validate(material_for(parsed, version))


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("first", [0, 1])
def test_valid_pre_fix_bytes_digests_and_indexing_remain_identical(version, first):
    record = COMPAT["cases"][f"{version}-{first}"]
    candidate = valid(version, first)
    assert candidate.model_dump_json() == record["candidate_json"]
    assert digest(candidate) == record["candidate_digest"]
    assert [p.page_index for p in candidate.pages] == [first, first + 1]
    material = PrivateReviewMaterial.model_validate_json(record["material_json"])
    assert material.model_dump_json() == record["material_json"]
    assert candidate.sensitivity == "D2" and not candidate.anonymous
    assert candidate.release_review_required and not candidate.automated_release_eligible


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("kind", ["duplicate", "empty", "bounds", "classification"])
def test_bad_typed_candidate_cannot_approve_or_export_and_does_not_consume(tmp_path, version, kind):
    candidate = valid(version)
    target = Destination(
        destination_id="test-private", destination_class="private", allowed_sensitivities=("D2",)
    )
    material = material_for(candidate, version)
    review = VerifiedReview(
        reviewer_subject="synthetic-final-reviewer",
        review_id="synthetic-review",
        evidence_digest=material.evidence_digest,
        candidate_digest=digest(candidate),
        sensitivity="D2",
        destination_id=target.destination_id,
        completed_at=int(time.time()),
    )
    verifier = Mock(return_value=review)
    ledger = SQLiteApprovalLedger(tmp_path / "ledger.sqlite", initialize=True)

    # Explicit synthetic policy callbacks; never production defaults.
    def policy(c, r):
        return digest(c) == review.candidate_digest

    authority = ReleaseAuthority(
        b"x" * 32,
        lambda *args: True,
        verify_review=verifier,
        consume_approval=ledger.consume,
        destinations=(target,),
        legacy_policy=policy,
        correction_policy=policy,
    )
    approval = authority.approve(
        candidate, actor="session", review_id=review.review_id, destination=target.destination_id
    )
    page = candidate.pages[0]
    if kind == "duplicate":
        pages = (page, page)
    elif kind == "empty":
        pages = ()
    elif kind == "bounds":
        pages = (
            page.model_copy(update={"tags": (page.tags[0].model_copy(update={"start": -1}),)}),
        ) + candidate.pages[1:]
    else:
        pages = (page.model_copy(update={"original_sensitivity": "D0"}),) + candidate.pages[1:]
    forged = candidate.model_copy(update={"pages": pages})
    verifier.reset_mock()
    for action in (
        lambda: authority.approve(
            forged, actor="session", review_id=review.review_id, destination=target.destination_id
        ),
        lambda: authority.export(
            forged, approval, actor="session", destination=target.destination_id
        ),
    ):
        with pytest.raises(SafeError, match="^invalid_release_candidate$"):
            action()
    verifier.assert_not_called()
    # Original exact-digest approval is still usable; malformed attempts did not
    # mutate old bytes/history or consume its durable nonce.
    output = json.loads(
        authority.export(candidate, approval, actor="session", destination=target.destination_id)
    )
    assert output["sensitivity"] == "D2" and not output["transmission_authorized"]
    with pytest.raises(SafeError, match="^release_approval_consumed_or_unavailable$"):
        authority.export(candidate, approval, actor="session", destination=target.destination_id)


@pytest.mark.parametrize("kind", ["foreign_page", "upper_bound"])
def test_private_review_and_correction_reject_corrupt_stored_material(kind):
    host = host_fixture()
    service = CorrectionService(host)
    proposal = service.propose(
        host.author_credential,
        "original-review",
        (edit_for(host, "Silver Lantern Archives", "ORGANIZATION", "incomplete_boundary"),),
    )
    host.context_reads = 0
    context = host.contexts["original-review"]
    candidate = context.material.candidate
    page = candidate.pages[0]
    if kind == "foreign_page":
        page = page.model_copy(update={"page_index": 19})
    else:
        tag = page.tags[0].model_copy(update={"end": context.machine.sources[0].characters + 1})
        page = page.model_copy(update={"tags": (tag,)})
    changed = type(candidate).model_validate(candidate.model_copy(update={"pages": (page,)}))
    host.candidates[digest(changed)] = changed
    material = context.material.model_copy(
        update={"candidate": changed, "candidate_digest": digest(changed)}
    )
    host.contexts["original-review"] = context.model_copy(update={"material": material})
    with pytest.raises(SafeError, match="^private_review_access_denied$"):
        PrivateReviewBridge(host).open(host.author_credential, "original-review")
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        service.inspect(host.author_credential, "original-review")
    with pytest.raises(SafeError, match="^private_correction_denied$"):
        service.apply(host.author_credential, proposal.correction_id)
    assert host.context_reads == 0 and len(host.contexts) == 1
    assert host.proposals[proposal.correction_id].published_candidate_digest is None


@pytest.mark.parametrize("version", VERSIONS)
def test_malformed_document_containers_and_classification_fail_closed(version):
    candidate = valid(version)
    for pages in (None, {}, "pages", [None]):
        data = json.loads(candidate.model_dump_json())
        data["pages"] = pages
        with pytest.raises(SafeError, match="^unsupported_or_invalid_candidate$"):
            parse_candidate(json.dumps(data))
    mismatch = candidate.model_copy(update={"sensitivity": "D0"})
    with pytest.raises(SafeError, match="^invalid_release_candidate$"):
        digest(mismatch)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_v3_duplicate_pages_cannot_bypass_with_version_coercion(version):
    corrected = valid("v3")
    malformed = corrected.model_copy(
        update={
            "schema_version": "ocr-anonymization-candidate/" + version,
            "pages": (corrected.pages[0], corrected.pages[0]),
        }
    )
    with pytest.raises(SafeError, match="^invalid_release_candidate$"):
        digest(malformed)
    with pytest.raises(SafeError, match="^unsupported_or_invalid_candidate$"):
        parse_candidate(malformed.model_dump_json())


def test_legacy_archive_unique_indices_are_not_forced_contiguous():
    legacy = valid("v1")
    archived = legacy.model_copy(
        update={
            "pages": tuple(
                page.model_copy(update={"page_index": index})
                for page, index in zip(legacy.pages, (7, 19), strict=True)
            )
        }
    )
    assert parse_candidate(archived.model_dump_json()) == archived
    assert digest(archived)
