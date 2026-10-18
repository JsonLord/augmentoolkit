"""Publish a complete private file atomically, without overwriting any target."""

import fcntl
import os
import tempfile
from pathlib import Path

from ocr.schemas import SafeError


def write_candidate(path, data):
    temporary, directory = None, None
    try:
        path = Path(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fcntl.flock(directory, fcntl.LOCK_SH)
        # Same-directory staging guarantees a same-filesystem atomic hard link.
        fd, temporary = tempfile.mkstemp(prefix=".anonymization-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            if stream.write(data) != len(data):
                raise OSError("incomplete_write")
            stream.flush()
            os.fsync(stream.fileno())
        # link() fails if ANY target (including a symlink) already exists.
        # Never use rename()/replace(), which can clobber another process's file.
        os.link(temporary, path)
    except Exception:
        raise SafeError("anonymization_candidate_write_failed") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                # Never print classified bytes or paths on cleanup failure. A
                # leftover staging file is private (0600), never an approved export.
                pass

        if directory is not None:
            os.close(directory)
