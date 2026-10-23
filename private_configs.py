"""Private YAML editing; bundled reads require exact operator-authorized aliases."""

import hashlib
import os

import yaml
from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response

import input_files

MAX_BYTES = 1024 * 1024


def etag(data):
    return '"' + hashlib.sha256(data).hexdigest() + '"'


def private_path(base, name):
    target = input_files.path(base, name, root=False)
    if target.suffix.lower() not in {".yaml", ".yml"}:
        raise HTTPException(400, "Expected a YAML filename")
    return target


def validate(data):
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Configuration exceeds 1 MiB")
    try:
        text = data.decode("utf-8", errors="strict")
        value = yaml.safe_load(text)
    except UnicodeDecodeError:
        raise HTTPException(400, "Configuration must be UTF-8") from None
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        detail = (
            f"Invalid YAML at line {mark.line + 1}, column {mark.column + 1}"
            if mark
            else "Invalid YAML"
        )
        raise HTTPException(400, detail) from None
    except RecursionError:
        raise HTTPException(400, "YAML nesting exceeds supported complexity") from None
    if not isinstance(value, dict):
        raise HTTPException(400, "Configuration YAML must be a mapping")
    return text


def bytes_from(target):
    if not target.is_file():
        raise HTTPException(404, "Configuration not found")
    if target.stat().st_size > MAX_BYTES:
        raise HTTPException(413, "Configuration exceeds 1 MiB")
    with os.fdopen(os.open(target, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Configuration exceeds 1 MiB")
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise HTTPException(400, "Configuration must be UTF-8") from None
    return data


@input_files.operation
def read(base, name, source=None):
    if source is None and not input_files.path(base, name, root=False).exists():
        raise HTTPException(404, "Configuration not found")
    target = source if source is not None else private_path(base, name)
    data = bytes_from(target)
    return Response(
        data,
        media_type="text/plain; charset=utf-8",
        headers={
            "ETag": etag(data),
            "X-Config-Read-Only": "true" if source is not None else "false",
            "X-Config-Origin": "bundled" if source is not None else "private",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def publish(target, data, *, replace):
    fd, tmp = input_files.temporary()
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(tmp, target)
        else:
            try:
                os.link(tmp, target)
            except FileExistsError:
                raise HTTPException(409, "Configuration already exists") from None
    finally:
        tmp.unlink(missing_ok=True)


@input_files.operation
def save(base, name, data, expected_etag, source=None):
    if source is not None:
        raise HTTPException(
            403, "Bundled templates are read-only; create a private copy"
        )
    target = private_path(base, name)
    validate(data)
    if not target.parent.is_dir():
        raise HTTPException(404, "Parent directory not found")
    exists = target.exists()
    if exists:
        if expected_etag != etag(bytes_from(target)):
            raise HTTPException(
                409, "Configuration changed or missing version; reload before saving"
            )
    elif expected_etag is not None:
        raise HTTPException(409, "Configuration no longer exists; reload before saving")
    publish(target, data, replace=exists)
    return JSONResponse(
        {"message": "Configuration saved", "etag": etag(data)},
        headers={"ETag": etag(data), "Cache-Control": "no-store"},
    )


@input_files.operation
def duplicate(base, source, destination):
    target = private_path(base, destination)
    if target.exists():
        raise HTTPException(409, "Configuration already exists")
    if not target.parent.is_dir():
        raise HTTPException(404, "Parent directory not found")
    data = bytes_from(source)
    validate(data)
    publish(target, data, replace=False)
    return {
        "message": "Private configuration copy created",
        "path": destination,
        "etag": etag(data),
    }
