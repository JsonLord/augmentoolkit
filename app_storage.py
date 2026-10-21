"""Storage shared by the API and queue worker; source code is never writable state."""

import os
from pathlib import Path


class StorageViolation(ValueError):
    pass


def confined_path(base: Path, value: str | Path) -> Path:
    """Reject escapes, sibling prefixes and symlinks, including dangling links."""
    base = Path(os.path.abspath(base))
    candidate = base / value
    try:
        candidate.resolve().relative_to(base.resolve())
    except (ValueError, OSError, RuntimeError):
        raise StorageViolation("Path outside authorized storage") from None
    # Reject links even when their current target is inside storage. Storage must
    # be owned by the service account, never concurrently writable by untrusted users.
    for component in (candidate, *candidate.parents):
        if component.is_symlink():
            raise StorageViolation("Symlink storage paths are forbidden")
    return candidate.resolve()


def data_directory(name: str = "") -> Path:
    root = Path(os.environ.get("ATK_DATA_ROOT", "/tmp/augmentoolkit"))
    path = confined_path(root, name)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise StorageViolation("Storage must have private permissions (0700)")
    return path


def normalize_mutable_paths(parameters):
    """Validate writable path overrides before enqueue and after merging config."""
    if not isinstance(parameters, dict):
        return parameters
    result = {}
    for key, value in parameters.items():
        writable = key in {
            "output_dir",
            "output_path",
            "intermediate_output_path",
            "cache_dir",
            "dataset_prepared_path",
            "generation_dir",
        } or key.endswith(("_output_dir", "_output_path"))
        if writable and value is not None:
            if not isinstance(value, str) or not value:
                raise StorageViolation("Invalid writable path")
            base = data_directory(
                "generation" if key == "generation_dir" else "outputs"
            )
            result[key] = str(confined_path(base, value))
        elif isinstance(value, dict):
            result[key] = normalize_mutable_paths(value)
        else:
            result[key] = value
    return result
