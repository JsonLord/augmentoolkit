"""Bounded local D0 text preparation, not Q&A generation or a general scheduler."""

import ctypes
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import uuid

from fastapi import HTTPException

import input_files
import private_configs
from app_storage import data_directory

MAX_INPUT = 1024 * 1024
MAX_RECORDS = 4096
MAX_OUTPUT = 16 * 1024 * 1024
TIMEOUT = 15
ARTIFACTS = {"records.jsonl", "manifest.json"}
SERVICE = None
SERVICE_LOCK = threading.Lock()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    # Private state uses same-filesystem staging; final state is published last.
    tmp = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    try:
        with os.fdopen(
            os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path, value):
    atomic(
        path, (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
    )


def load_state(directory):
    try:
        path = directory / "state.json"
        if path.is_symlink() or path.stat().st_size > 64 * 1024:
            raise ValueError
        value = json.loads(path.read_bytes())
        if value.get("job_id") != directory.name or value.get("status") not in {
            "RUNNING",
            "FAILED",
            "COMPLETED",
        }:
            raise ValueError
        if (
            value["status"] == "COMPLETED"
            and set(value.get("artifact_hashes", {})) != ARTIFACTS
        ):
            raise ValueError
        return value
    except (OSError, ValueError, AttributeError):
        raise HTTPException(
            503, "Preparation state requires operator recovery"
        ) from None


def identifier(value):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Invalid preparation identifier") from None
    return value


def parameters(raw):
    # Pilot config parsing is bounded before constructing objects. Native YAML
    # settings may be present but aliases/deep structures are unsupported here.
    import yaml

    if len(raw) > 16 * 1024:
        raise HTTPException(413, "Preparation configuration exceeds 16 KiB")
    depth = 0
    try:
        for index, event in enumerate(yaml.parse(raw)):
            if index > 4096 or isinstance(event, yaml.events.AliasEvent):
                raise HTTPException(
                    400, "Preparation YAML aliases/complexity unsupported"
                )
            if isinstance(
                event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)
            ):
                depth += 1
                if depth > 16:
                    raise HTTPException(400, "Preparation YAML nesting unsupported")
            elif isinstance(
                event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)
            ):
                depth -= 1
    except (yaml.YAMLError, UnicodeError):
        raise HTTPException(400, "Invalid preparation YAML") from None
    private_configs.validate(raw)
    value = yaml.safe_load(raw).get("preparation", {})
    if not isinstance(value, dict) or set(value) - {"chunk_chars", "overlap_chars"}:
        raise HTTPException(400, "Unsupported preparation configuration")
    size, overlap = value.get("chunk_chars", 1024), value.get("overlap_chars", 0)
    if (
        type(size) is not int
        or type(overlap) is not int
        or not 64 <= size <= 4096
        or not 0 <= overlap < min(size, 256)
    ):
        raise HTTPException(400, "Invalid chunk bounds")
    return {"chunk_chars": size, "overlap_chars": overlap}


def records(raw, config_raw, source_name):
    text = unicodedata.normalize(
        "NFC", raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    )
    if not text.strip() or "\x00" in text:
        raise ValueError("Invalid text")
    cfg = parameters(config_raw)
    source_sha, config_sha = digest(raw), digest(config_raw)
    stride = cfg["chunk_chars"] - cfg["overlap_chars"]
    if (
        1 + max(0, (len(text) - cfg["chunk_chars"] + stride - 1) // stride)
        > MAX_RECORDS
    ):
        raise ValueError("Too many chunks")
    result = []
    for index, start in enumerate(
        range(0, len(text), cfg["chunk_chars"] - cfg["overlap_chars"])
    ):
        end = min(start + cfg["chunk_chars"], len(text))
        result.append(
            {
                "kind": "preparation_chunk",
                "sensitivity": "D0",
                "source_filename": source_name,
                "source_sha256": source_sha,
                "config_revision": config_sha,
                "source_id": source_sha,
                "chunk_id": digest(f"{source_sha}:{config_sha}:{start}:{end}".encode()),
                "index": index,
                "normalized_start": start,
                "normalized_end": end,
                "text": text[start:end],
            }
        )
        if end == len(text):
            break
    return result


def execute(directory, output):
    state = load_state(directory)
    raw, cfg = (
        (directory / "source.txt").read_bytes(),
        (directory / "config.yaml").read_bytes(),
    )
    if (
        len(raw) > MAX_INPUT
        or len(cfg) > private_configs.MAX_BYTES
        or digest(raw) != state["source_sha256"]
        or digest(cfg) != state["config_revision"]
    ):
        raise ValueError("Snapshot integrity failure")
    rows = records(raw, cfg, state["source_filename"])
    data = b"".join(
        (json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n").encode()
        for r in rows
    )
    if len(data) > MAX_OUTPUT:
        raise ValueError("Output exceeds limit")
    output.mkdir(mode=0o700)
    atomic(output / "records.jsonl", data)
    manifest = {
        key: state[key]
        for key in [
            "job_id",
            "source_filename",
            "source_sha256",
            "config_revision",
            "sensitivity",
        ]
    }
    manifest.update(
        kind="deterministic_preparation",
        record_count=len(rows),
        records_sha256=digest(data),
        source_bytes=len(raw),
        normalization="UTF-8 BOM removal, CR/CRLF to LF, NFC; offsets refer to normalized text",
        source_revision=os.environ.get("OCR_SOURCE_REVISION", "unknown"),
    )
    write_json(output / "manifest.json", manifest)
    state.update(
        status="COMPLETED",
        phase="ready",
        record_count=len(rows),
        artifact_hashes={
            "records.jsonl": digest(data),
            "manifest.json": digest((output / "manifest.json").read_bytes()),
        },
    )
    write_json(directory / "state.json", state)


class Preparation:
    """One active child, no queue. Recovery never converts RUNNING to success."""

    def __init__(self):
        self.lock = threading.RLock()
        self.active = None
        self.runtime_errors = {}
        self.root = data_directory("pilot_state")
        self.output = data_directory("outputs/preparation")
        for directory in self.root.iterdir():
            input_files.path(self.root, directory.name)
            if directory.is_dir():
                input_files.inspect_tree(self.root, directory)
                try:
                    state = load_state(directory)
                except (OSError, ValueError):
                    raise HTTPException(
                        503, "Preparation state requires operator recovery"
                    ) from None
                if state["status"] == "RUNNING":
                    state.update(
                        status="FAILED",
                        phase="interrupted",
                        error="Interrupted by application restart",
                    )
                    write_json(directory / "state.json", state)
                    shutil.rmtree(self.output / directory.name, ignore_errors=True)
                for name in ["source.txt", "config.yaml"]:
                    (directory / name).unlink(missing_ok=True)

    def status(self, job_id):
        with self.lock:
            identifier(job_id)
            if job_id in self.runtime_errors:
                raise HTTPException(503, "Preparation state requires operator recovery")
            directory = input_files.path(self.root, job_id, root=False)
            if not directory.is_dir():
                raise HTTPException(404, "Preparation not found")
            state = load_state(directory)
            return {k: v for k, v in state.items() if k != "artifact_hashes"}

    def submit(self, inputs, configs, source_name, config_name, sensitivity):
        if sensitivity != "D0":
            raise HTTPException(400, "Explicit D0 classification required")
        with self.lock, input_files.LOCK:
            if self.active is not None:
                raise HTTPException(409, "A preparation task is active")
            source = input_files.path(inputs, source_name, root=False)
            config = private_configs.private_path(configs, config_name)
            if source.suffix.lower() != ".txt" or not source.is_file():
                raise HTTPException(400, "Select a private UTF-8 TXT input")
            if source.stat().st_size > MAX_INPUT:
                raise HTTPException(413, "TXT exceeds 1 MiB")
            with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), "rb") as f:
                raw = f.read(MAX_INPUT + 1)
            if len(raw) > MAX_INPUT:
                raise HTTPException(413, "TXT exceeds 1 MiB")
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise HTTPException(400, "TXT must be UTF-8") from None
            if not text.strip() or "\x00" in text:
                raise HTTPException(400, "TXT is empty or contains NUL")
            cfg = private_configs.bytes_from(config)
            parameters(cfg)
            job_id = str(uuid.uuid4())
            directory = self.root / job_id
            directory.mkdir(mode=0o700)
            state = {
                "job_id": job_id,
                "status": "RUNNING",
                "phase": "chunking",
                "sensitivity": "D0",
                "source_filename": source_name,
                "source_sha256": digest(raw),
                "config_revision": digest(cfg),
                "record_count": 0,
                "created_at": time.time(),
            }
            try:
                atomic(directory / "source.txt", raw)
                atomic(directory / "config.yaml", cfg)
                write_json(directory / "state.json", state)
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "pilot_prepare",
                        "--worker",
                        job_id,
                        str(os.getpid()),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                shutil.rmtree(directory, ignore_errors=True)
                raise HTTPException(503, "Preparation could not start") from None
            self.active = job_id
            try:
                threading.Thread(
                    target=self.watch, args=(job_id, process), daemon=True
                ).start()
            except RuntimeError:
                process.kill()
                process.wait()
                state.update(
                    status="FAILED",
                    phase="failed",
                    error="Preparation supervision unavailable",
                )
                write_json(directory / "state.json", state)
                for name in ["source.txt", "config.yaml"]:
                    (directory / name).unlink(missing_ok=True)
                shutil.rmtree(self.output / job_id, ignore_errors=True)
                self.active = None
                raise HTTPException(
                    503, "Preparation supervision unavailable"
                ) from None
            return state

    def watch(self, job_id, process):
        timed_out = False
        try:
            process.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        with self.lock:
            directory = self.root / job_id
            try:
                state = load_state(directory)
            except HTTPException:
                self.runtime_errors[job_id] = True
                shutil.rmtree(self.output / job_id, ignore_errors=True)
                for name in ["source.txt", "config.yaml"]:
                    try:
                        (directory / name).unlink(missing_ok=True)
                    except OSError:
                        pass
                self.active = None
                return
            if timed_out or process.returncode != 0 or state["status"] != "COMPLETED":
                state.update(
                    status="FAILED",
                    phase="failed",
                    error="Preparation timed out"
                    if timed_out
                    else "Preparation failed",
                )
                write_json(directory / "state.json", state)
                shutil.rmtree(self.output / job_id, ignore_errors=True)
            for name in ["source.txt", "config.yaml"]:
                (directory / name).unlink(missing_ok=True)
            self.active = None

    def list(self):
        with self.lock:
            return [
                self.status(p.name) for p in sorted(self.root.iterdir()) if p.is_dir()
            ]

    def artifact(self, job_id, name):
        with self.lock:
            if name not in ARTIFACTS:
                raise HTTPException(404, "Artifact not found")
            self.status(job_id)
            state = load_state(self.root / job_id)
            if state["status"] != "COMPLETED":
                raise HTTPException(409, "Preparation is not complete")
            path = input_files.path(self.output, job_id + "/" + name, root=False)
            data = path.read_bytes()
            if digest(data) != state["artifact_hashes"][name]:
                raise HTTPException(409, "Artifact integrity failure")
            return path, data


def service():
    global SERVICE
    with SERVICE_LOCK:
        if SERVICE is None:
            SERVICE = Preparation()
        return SERVICE


def worker(job_id, parent):
    # Linux parent-death binding: a web restart cannot leave an orphan publisher.
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL) != 0 or os.getppid() != parent:
        raise RuntimeError("Parent binding failed")
    root = data_directory("pilot_state")
    directory = input_files.path(root, identifier(job_id), root=False)
    execute(directory, data_directory("outputs/preparation") / job_id)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    worker(sys.argv[2], int(sys.argv[3]))
