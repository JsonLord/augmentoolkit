"""Strict Dataset operations added to HFHubManager, separate from legacy helpers."""

import hashlib
import re
from pathlib import Path, PurePosixPath

import httpx
from huggingface_hub import CommitOperationAdd, hf_hub_download
from huggingface_hub.utils import HfHubHTTPError
from requests.exceptions import RequestException

from .hf_manager import HFHubError

REVISION = re.compile(r"^[0-9a-f]{40}$")


class DatasetConflict(HFHubError):
    pass


class DatasetIntegrityError(HFHubError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_path(name):
    if (
        not isinstance(name, str)
        or PurePosixPath(name).is_absolute()
        or "\\" in name
        or any(p in {"", ".", ".."} for p in name.split("/"))
        or any(ord(c) < 32 for c in name)
    ):
        raise HFHubError("Invalid Dataset object path")


def private_head(manager, repo):
    if not manager.token:
        raise HFHubError("Explicit HF_TOKEN binding is required")
    if not repo or repo not in {
        manager.source_repo,
        manager.work_repo,
        manager.output_repo,
    }:
        raise HFHubError("Dataset repository role is not configured")
    if getattr(manager.api, "endpoint", "").rstrip("/") != "https://huggingface.co":
        raise HFHubError("Unapproved Hub endpoint")
    try:
        info = manager.api.repo_info(
            repo_id=repo, repo_type="dataset", token=manager.token
        )
        if info.private is not True or not REVISION.fullmatch(info.sha):
            raise HFHubError("Private Dataset repository and exact revision required")
        return info.sha
    except HFHubError:
        raise
    except (OSError, ValueError, AttributeError, RequestException, httpx.HTTPError):
        raise HFHubError("Dataset repository unavailable or unauthorized") from None


def read_verified(
    manager, repo, name, revision, expected, cache, limit=20 * 1024 * 1024
):
    valid_path(name)
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise HFHubError("Pinned Dataset revision required")
    private_head(manager, repo)
    try:
        downloaded = hf_hub_download(
            repo_id=repo,
            repo_type="dataset",
            filename=name,
            revision=revision,
            token=manager.token,
            endpoint="https://huggingface.co",
            cache_dir=str(cache),
        )
        path = Path(downloaded)
        if path.stat().st_size > limit:
            raise DatasetIntegrityError("Dataset object exceeds limit")
        data = path.read_bytes()
        if expected is not None and digest(data) != expected:
            raise DatasetIntegrityError("Dataset checksum mismatch")
        return data
    except HFHubError:
        raise
    except (OSError, ValueError, AttributeError, RequestException, httpx.HTTPError):
        raise HFHubError("Pinned Dataset object unavailable") from None


def commit_verified(manager, repo, parent, files, cache, mutable=()):
    if not isinstance(parent, str) or not REVISION.fullmatch(parent):
        raise HFHubError("Exact parent commit required")
    if (
        not 1 <= len(files) <= 4
        or sum(len(b) for b in files.values()) > 20 * 1024 * 1024
    ):
        raise HFHubError("Dataset commit bounds exceeded")
    for name in files:
        valid_path(name)
    head = private_head(manager, repo)
    if head != parent:
        # Explicit lost-ack reconciliation: only adopt an already-present exact
        # batch, never retry a new write against an unrelated changed head.
        for name, data in files.items():
            try:
                read_verified(manager, repo, name, head, digest(data), cache)
            except HFHubError:
                raise DatasetConflict(
                    "Dataset head changed; explicit reconciliation required"
                ) from None
        return {"revision": head, "reconciled": True}
    try:
        all_identical = True
        for name, data in files.items():
            exists = manager.api.file_exists(
                repo_id=repo,
                repo_type="dataset",
                filename=name,
                revision=parent,
                token=manager.token,
            )
            if not exists:
                all_identical = False
            else:
                try:
                    read_verified(manager, repo, name, parent, digest(data), cache)
                except DatasetIntegrityError:
                    if name not in mutable:
                        raise
                    all_identical = False
        if all_identical:
            return {"revision": parent, "reconciled": True}
        result = manager.api.create_commit(
            repo_id=repo,
            repo_type="dataset",
            parent_commit=parent,
            token=manager.token,
            operations=[
                CommitOperationAdd(path_in_repo=n, path_or_fileobj=b)
                for n, b in files.items()
            ],
            commit_message="D0 preparation bounded artifact commit",
        )
        revision = result.oid
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise HFHubError("Hub did not return an exact commit revision")
        for name, data in files.items():
            read_verified(manager, repo, name, revision, digest(data), cache)
        return {"revision": revision, "reconciled": False}
    except HFHubError:
        raise
    except HfHubHTTPError as error:
        if getattr(error.response, "status_code", None) in {409, 412}:
            raise DatasetConflict("Dataset parent commit conflicted") from None
        raise HFHubError("Dataset commit unavailable or unauthorized") from None
    except (OSError, ValueError, AttributeError, RequestException, httpx.HTTPError):
        raise HFHubError(
            "Dataset commit failed or acknowledgement was interrupted"
        ) from None
