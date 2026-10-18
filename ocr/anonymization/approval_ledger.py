"""Explicitly provisioned, persistent single-host SQLite replay ledger."""

import hashlib
import os
import sqlite3
import stat
import time
import uuid
from contextlib import closing
from pathlib import Path

from ocr.schemas import SafeError


class SQLiteApprovalLedger:
    """Provision once with initialize=True; ordinary startup NEVER creates storage.

    Persist ledger_id in private configuration and supply expected_id on restart.
    Local ledgers must not share a signing key across hosts. A genuinely shared
    durable backend is required for a shared key domain, not SQLite copies/NFS.
    """

    def __init__(self, path, *, initialize=False, expected_id=None):
        self.path = Path(path).absolute()
        try:
            if initialize:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            self._metadata()
            with closing(self._connect()) as connection, connection:
                if initialize:
                    connection.execute("PRAGMA synchronous=FULL")
                    connection.execute(
                        "CREATE TABLE consumed (id TEXT PRIMARY KEY, expires INTEGER NOT NULL)"
                    )
                    connection.execute("CREATE TABLE identity (id TEXT NOT NULL)")
                    connection.execute("INSERT INTO identity VALUES (?)", (uuid.uuid4().hex,))
                if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise ValueError()
                self.ledger_id = connection.execute("SELECT id FROM identity").fetchone()[0]
                if expected_id is not None and expected_id != self.ledger_id:
                    raise ValueError()
            metadata = self.path.lstat()
            self._inode = (metadata.st_dev, metadata.st_ino)
        except Exception:
            raise SafeError("approval_ledger_unavailable") from None

    def _metadata(self):
        metadata, parent = self.path.lstat(), self.path.parent.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
        ):
            raise ValueError()
        return metadata

    def _connect(self):
        return sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)

    def consume(self, approval_id, expires_at):
        try:
            now = int(time.time())
            if type(expires_at) is not int or expires_at <= now:
                return False
            metadata = self._metadata()
            if (metadata.st_dev, metadata.st_ino) != self._inode:
                raise ValueError()
            with closing(self._connect()) as connection, connection:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                # Lock acquisition may have waited beyond approval expiration.
                # Recheck at the consumption transaction, not only before waiting.
                if expires_at <= int(time.time()):
                    return False
                if connection.execute("SELECT id FROM identity").fetchone() != (self.ledger_id,):
                    raise ValueError()
                # Retain consumed IDs: clock rollback must not resurrect approvals
                # after expiry-based garbage collection. Offline archive is operator work.
                connection.execute("INSERT INTO consumed VALUES (?, ?)", (approval_id, expires_at))
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception:
            raise SafeError("approval_ledger_unavailable") from None


def validate_key_topology(bindings, *, signing_keys):
    """Compare actual key fingerprints, never caller-chosen key aliases.

    This implementation qualifies local per-host key domains only. A shared
    backend declaration is NOT evidence of shared durability; that integration
    remains blocked until a real backend is implemented and qualified separately.
    """
    try:
        groups = {}
        for entry in bindings:
            key = signing_keys[entry["key_id"]]
            if not isinstance(key, bytes) or len(key) < 32:
                raise ValueError()
            fingerprint = hashlib.sha256(key).hexdigest()
            groups.setdefault(fingerprint, set()).add(entry["host_id"])
        if any(len(hosts) > 1 for hosts in groups.values()):
            raise ValueError()
    except Exception:
        raise SafeError("shared_key_multi_host_backend_not_qualified") from None
