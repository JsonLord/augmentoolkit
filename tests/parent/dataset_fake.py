"""Disk-backed fake Hub used only by isolated tests, never the application."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from augmentoolkit.hf_persistence.dataset_commit import DatasetConflict
from augmentoolkit.hf_persistence.hf_manager import HFHubManager


class FakeAPI:
    endpoint = "https://huggingface.co"

    def __init__(self, root):
        self.root = Path(root)
        self.failure = None
        self.failure_repo = None
        self.failure_path = None
        self.corrupt = False
        self.calls = []

    def initialize(self, repos):
        self.root.mkdir(mode=0o700)
        for repo in repos:
            directory = self.directory(repo)
            directory.mkdir(parents=True)
            (directory / ("0" * 40)).mkdir()
            (directory / "head.json").write_text(
                json.dumps({"sha": "0" * 40, "private": True})
            )

    def directory(self, repo):
        return self.root / repo.replace("/", "__")

    def repo_info(self, repo_id, repo_type, **kwargs):
        assert repo_type == "dataset"
        return SimpleNamespace(
            **json.loads((self.directory(repo_id) / "head.json").read_bytes())
        )

    def file_exists(self, repo_id, filename, revision, repo_type, **kwargs):
        assert repo_type == "dataset"
        return (self.directory(repo_id) / revision / filename).is_file()

    def list_repo_files(self, repo_id, repo_type, revision, **kwargs):
        assert repo_type == "dataset"
        root = self.directory(repo_id) / revision
        return [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]

    def create_commit(self, repo_id, repo_type, parent_commit, operations, **kwargs):
        assert repo_type == "dataset"
        self.calls.append(
            (repo_id, parent_commit, [o.path_in_repo for o in operations])
        )
        matches = (self.failure_repo is None or self.failure_repo == repo_id) and (
            self.failure_path is None
            or any(self.failure_path in o.path_in_repo for o in operations)
        )
        failure = self.failure if matches else None
        if matches:
            self.failure = None
        if failure == "before":
            raise OSError("secret credential should never reach an error response")
        info = self.repo_info(repo_id, repo_type)
        if info.sha != parent_commit:
            raise DatasetConflict("Conflict")
        directory = self.directory(repo_id)
        count = len([p for p in directory.iterdir() if p.is_dir()])
        revision = f"{count:040x}"
        shutil.copytree(directory / parent_commit, directory / revision)
        for operation in operations:
            target = directory / revision / operation.path_in_repo
            target.parent.mkdir(parents=True, exist_ok=True)
            assert isinstance(operation.path_or_fileobj, bytes)
            target.write_bytes(operation.path_or_fileobj)
        (directory / "head.json").write_text(
            json.dumps({"sha": revision, "private": True})
        )
        if failure == "lost_ack":
            raise OSError("secret lost acknowledgement")
        return SimpleNamespace(oid=revision)

    def download(self, repo_id, filename, revision, repo_type, **kwargs):
        assert repo_type == "dataset"
        path = self.directory(repo_id) / revision / filename
        if self.corrupt:
            wrong = self.root / "corrupted-response"
            wrong.write_bytes(b"corrupted synthetic response")
            return str(wrong)
        if not path.is_file():
            raise FileNotFoundError("Missing artifact")
        return str(path)


def manager(root):
    hub = HFHubManager(
        token="synthetic",
        source_repo="fixture/source",
        work_repo="fixture/work",
        output_repo="fixture/output",
    )
    hub.api = FakeAPI(root)
    return hub
