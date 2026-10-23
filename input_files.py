"""Private input-file operations for the single-process parent application.

Storage must be service-owned and not writable by other local users/processes.
The lock serializes API mutations; it is not a multi-host transaction ledger.
"""

import os
import shutil
import tempfile
import threading
from functools import wraps
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from app_storage import data_directory
from file_operation_helpers import FileStructure, get_safe_path, zip_directory

MAX_UPLOAD_BYTES = 16 * 1024 * 1024  # aggregate file bytes per batch
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
LOCK = threading.RLock()


def operation(function):
    @wraps(function)
    def safe(*args, **kwargs):
        with LOCK:
            try:
                return function(*args, **kwargs)
            except FileNotFoundError:
                raise HTTPException(404, "Item not found") from None
            except (NotADirectoryError, IsADirectoryError):
                raise HTTPException(400, "Invalid file type") from None
            except OSError:
                raise HTTPException(500, "Storage operation failed") from None

    return safe


def path(base, relative, *, root=True):
    value = str(relative)
    if (
        Path(value).is_absolute()
        or "\\" in value
        or any(ord(c) < 32 for c in value)
        or ".." in value.split("/")
    ):
        raise HTTPException(400, "Invalid storage path")
    target = get_safe_path(base, value or ".")
    if not root and target == base.resolve():
        raise HTTPException(400, "Root operation forbidden")
    if base.stat().st_uid != os.geteuid() or base.stat().st_mode & 0o077:
        raise HTTPException(503, "Storage ownership or permissions invalid")
    return target


def inspect_tree(base, target):
    """Validate every descendant before delete/archive; never follow links."""
    path(base, target.relative_to(base))
    if target.is_dir():
        for child in target.iterdir():
            inspect_tree(base, child)
    elif not target.is_file():
        raise HTTPException(400, "Unsupported file type")


@operation
def tree(base, relative="."):
    with LOCK:
        target = path(base, relative)
        if not target.exists():
            raise HTTPException(404, "Directory not found")
        if not target.is_dir():
            raise HTTPException(400, "Expected directory")
        inspect_tree(base, target)

        def nodes(directory):
            return [
                FileStructure(
                    path=str(p.relative_to(base)),
                    is_dir=p.is_dir(),
                    children=nodes(p) if p.is_dir() else None,
                )
                for p in sorted(directory.iterdir())
            ]

        return nodes(target)


def temporary():
    fd, name = tempfile.mkstemp(prefix="private-", dir=data_directory("file_staging"))
    return fd, Path(name)


@operation
def upload(base, relative, files):
    staged, published = [], []
    try:
        with LOCK:
            directory = path(base, relative)
            if not directory.is_dir():
                raise HTTPException(404, "Upload directory not found")
            if not files or len(files) > 100:
                raise HTTPException(400, "Expected 1 to 100 files")
            names = [f.filename or "" for f in files]
            if len(set(names)) != len(names):
                raise HTTPException(409, "Duplicate batch filename")
            for name in names:
                if not name or name in {".", ".."} or "/" in name or "\\" in name:
                    raise HTTPException(400, "Expected plain filename")
                target = path(base, str(directory.relative_to(base) / name), root=False)
                if target.exists():
                    raise HTTPException(409, "Destination already exists")
            total = 0
            for item, name in zip(files, names, strict=True):
                fd, tmp = temporary()
                staged.append(tmp)
                with os.fdopen(fd, "wb") as out:
                    while chunk := item.file.read(64 * 1024):
                        total += len(chunk)
                        if total > MAX_UPLOAD_BYTES:
                            raise HTTPException(413, "Upload batch exceeds 16 MiB")
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
            # Hard-link publication atomically refuses an existing destination.
            for tmp, name in zip(staged, names, strict=True):
                target = path(base, str(directory.relative_to(base) / name), root=False)
                os.link(tmp, target)
                published.append(target)
            return {"message": "Upload complete", "uploaded_files": names, "errors": []}
    except FileExistsError:
        for p in published:
            p.unlink(missing_ok=True)
        raise HTTPException(409, "Destination already exists") from None
    except BaseException:
        for p in published:
            p.unlink(missing_ok=True)
        raise
    finally:
        for p in staged:
            p.unlink(missing_ok=True)
        for f in files:
            f.file.close()


@operation
def create_directory(base, relative):
    with LOCK:
        target = path(base, relative, root=False)
        if not target.parent.is_dir():
            raise HTTPException(404, "Parent directory not found")
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            raise HTTPException(409, "Destination already exists") from None
        return {"message": "Directory created"}


@operation
def move(base, source, destination):
    with LOCK:
        src, dst = path(base, source, root=False), path(base, destination, root=False)
        if not src.exists():
            raise HTTPException(404, "Source not found")
        inspect_tree(base, src)
        if dst.exists():
            raise HTTPException(409, "Destination already exists")
        if src in dst.parents:
            raise HTTPException(400, "Cannot move a directory into itself")
        if not dst.parent.is_dir():
            raise HTTPException(404, "Parent directory not found")
        # One process, private service-owned storage, all API writes under LOCK.
        src.rename(dst)
        return {"message": "Item moved"}


@operation
def delete(base, relative):
    with LOCK:
        target = path(base, relative, root=False)
        if not target.exists():
            raise HTTPException(404, "Item not found")
        inspect_tree(base, target)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"message": "Item deleted"}


@operation
def download(base, relative):
    with LOCK:
        target = path(base, relative)
        if not target.exists():
            raise HTTPException(404, "Item not found")
        inspect_tree(base, target)
        fd, tmp = temporary()
        os.close(fd)
        try:
            if target.is_dir():
                if (
                    sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
                    > MAX_ARCHIVE_BYTES
                ):
                    raise HTTPException(413, "Archive exceeds 64 MiB")
                zip_directory(target, tmp)
                filename, media = target.name + ".zip", "application/zip"
            else:
                # Snapshot under the mutation lock; FileResponse must not reopen
                # the caller's mutable path after authorization/validation.
                with (
                    os.fdopen(
                        os.open(target, os.O_RDONLY | os.O_NOFOLLOW), "rb"
                    ) as src,
                    tmp.open("wb") as out,
                ):
                    shutil.copyfileobj(src, out)
                filename, media = target.name, "application/octet-stream"
            return FileResponse(
                tmp,
                filename=filename,
                media_type=media,
                background=BackgroundTask(tmp.unlink, missing_ok=True),
                headers={
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-store",
                },
            )
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


class InputUploadLimit:
    """Bound multipart bytes before Starlette can spool an oversized request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith("/inputs/upload/")
        ):
            return await self.app(scope, receive, send)
        limit = MAX_UPLOAD_BYTES + 64 * 1024  # bounded multipart overhead
        headers = dict(scope.get("headers", []))
        try:
            too_large = int(headers.get(b"content-length", b"0")) > limit
        except ValueError:
            return await JSONResponse(
                {"detail": "Invalid content length"}, status_code=400
            )(scope, receive, send)
        body = bytearray()
        while not too_large:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            too_large = len(body) > limit
            if not message.get("more_body", False):
                break
        if too_large:
            return await JSONResponse(
                {"detail": "Upload request too large"}, status_code=413
            )(scope, receive, send)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, bounded_receive, send)
