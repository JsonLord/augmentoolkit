"""Explicit local private delivery only. No URL client, redirects or fallback."""

import fcntl
import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import Field

from ocr.schemas import SafeError

from .schemas import Destination, StrictModel


class ReviewedExport(StrictModel):
    schema_version: Literal["ocr-reviewed-export/v2"]
    text: str
    sensitivity: Literal["D0", "D1", "D2"]
    anonymous: Literal[False]
    release_review_required: Literal[False]
    release_approved: Literal[True]
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    destination_id: str = Field(pattern=r"^[a-zA-Z0-9:_-]{1,128}$")
    destination_class: Literal["private"]
    transmission_authorized: Literal[False]


@dataclass(frozen=True)
class LocalDestination:
    policy: Destination
    root: Path


class PrivateFileTransport:
    def __init__(self, destinations, *, authorize_transfer):
        if not callable(authorize_transfer):
            raise SafeError("private_transport_not_configured")
        self._authorize = authorize_transfer
        registry = {}
        for entry in destinations:
            try:
                root = Path(entry.root)
                if (
                    not root.is_absolute()
                    or root.resolve() != root
                    or entry.policy.destination_class != "private"
                ):
                    raise ValueError()
                fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    metadata = os.fstat(fd)
                    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                        raise ValueError()
                finally:
                    os.close(fd)
                if entry.policy.destination_id in registry:
                    raise ValueError()
                registry[entry.policy.destination_id] = (entry, metadata.st_dev, metadata.st_ino)
            except Exception:
                raise SafeError("private_destination_unavailable") from None
        self._registry = MappingProxyType(registry)

    def deliver(self, exported_json, *, credential, destination_id):
        directory, temporary = None, None
        try:
            if (
                destination_id not in self._registry
                or not isinstance(exported_json, str)
                or len(exported_json) > 8 * 1024 * 1024
            ):
                raise ValueError()
            payload = ReviewedExport.model_validate_json(exported_json).model_dump()
            entry, device, inode = self._registry[destination_id]
            if (
                payload.get("schema_version") != "ocr-reviewed-export/v2"
                or payload.get("release_approved") is not True
                or payload.get("transmission_authorized") is not False
                or payload.get("destination_id") != destination_id
                or payload.get("destination_class") != "private"
                or payload.get("sensitivity") not in entry.policy.allowed_sensitivities
            ):
                raise ValueError()
            data = exported_json.encode()
            artifact_digest = hashlib.sha256(data).hexdigest()
            if (
                self._authorize(credential, artifact_digest, payload["sensitivity"], destination_id)
                is not True
            ):
                raise ValueError()
            directory = os.open(entry.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            fcntl.flock(directory, fcntl.LOCK_SH)
            metadata = os.fstat(directory)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_mode & 0o077
                or metadata.st_uid != os.getuid()
                or (metadata.st_dev, metadata.st_ino) != (device, inode)
            ):
                raise ValueError()
            temporary = ".private-transfer-" + uuid.uuid4().hex
            fd = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(fd, "wb") as stream:
                if stream.write(data) != len(data):
                    raise ValueError()
                stream.flush()
                os.fsync(stream.fileno())
            os.link(
                temporary,
                artifact_digest + ".json",
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            os.fsync(directory)
            return dict(
                destination_id=destination_id,
                artifact_digest=artifact_digest,
                sensitivity=payload["sensitivity"],
                transport="local-private-file",
                automated_release_eligible=False,
            )
        except Exception:
            raise SafeError("private_transfer_denied_or_failed") from None
        finally:
            if directory is not None:
                if temporary is not None:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except OSError:
                        pass
                os.close(directory)
