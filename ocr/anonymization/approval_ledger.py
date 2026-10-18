"""Optional local SQLite replay ledger; no service, identity provider or transport."""

import os
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path

from ocr.schemas import SafeError


class SQLiteApprovalLedger:
    """Share the SAME persistent ledger across every authority using a signing key.

    The parent directory must be controlled by the private application. Do not
    delete/replace the ledger while approvals remain valid. SQLite provides atomic
    consumption across threads/processes; in-memory sets cannot provide this gate.
    """

    def __init__(self, path):
        self.path = Path(path)
        try:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
            metadata = self.path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
            ):
                raise ValueError()
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS consumed (id TEXT PRIMARY KEY, expires INTEGER NOT NULL)"
                )
        except Exception:
            raise SafeError("approval_ledger_unavailable") from None

    def consume(self, approval_id, expires_at):
        try:
            now = int(time.time())
            if type(expires_at) is not int or expires_at <= now:
                return False
            with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM consumed WHERE expires <= ?", (now,))
                connection.execute("INSERT INTO consumed VALUES (?, ?)", (approval_id, expires_at))
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception:
            raise SafeError("approval_ledger_unavailable") from None
