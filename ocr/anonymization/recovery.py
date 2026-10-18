"""Explicit classified staging recovery; no source-evidence deletion or logging."""

import fcntl
import os
import re
import stat
import time

from ocr.schemas import SafeError

STAGING = re.compile(r"\.(?:anonymization|private-transfer)-[A-Za-z0-9_-]{6,64}")


def recover_staging(directory, *, minimum_age_seconds=3600):
    descriptor = None
    try:
        if type(minimum_age_seconds) is not int or minimum_age_seconds < 60:
            raise ValueError()
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError()
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        removed = 0
        for name in os.listdir(descriptor):
            if not STAGING.fullmatch(name):
                continue
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid()
                and not info.st_mode & 0o077
                and time.time() - info.st_mtime >= minimum_age_seconds
            ):
                os.unlink(name, dir_fd=descriptor)
                removed += 1
        os.fsync(descriptor)
        return dict(removed_staging_files=removed, secure_erasure_guaranteed=False)
    except Exception:
        raise SafeError("classified_staging_recovery_failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
