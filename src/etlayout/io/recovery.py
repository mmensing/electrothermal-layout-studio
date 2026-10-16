# SPDX-License-Identifier: MPL-2.0
"""Local, per-session crash recovery with a rebuildable atomic catalogue.

Snapshots and receipts are unique and durable before catalogue publication. The
OS releases session leases after crashes; PID reuse is therefore irrelevant.
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from etlayout.io.identity import document_id
from etlayout.io.project import save_project
from etlayout.models import SimulationError

MAX_INDEX_BYTES = 4_000_000
MAX_RECOVERY_ENTRIES = 10_000


@contextmanager
def file_lock(path: Path, *, blocking: bool = True):
    """One-byte advisory OS lock, retained by its open file description."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if path.stat().st_size == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            # LK_LOCK has an undocumented-to-callers finite retry period. Use
            # bounded retries explicitly so a dead catalogue owner never hangs.
            deadline = time.monotonic() + (15 if blocking else 0)
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if not blocking or time.monotonic() >= deadline:
                        raise
                    time.sleep(.025)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        locked = True
        yield stream
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class RecoveryEntry:
    document_id: str
    session_id: str
    snapshot_id: str
    revision: int
    name: str
    original_path: str
    snapshot: str
    saved_at: float
    status: str = "recoverable"

    @classmethod
    def decode(cls, value):
        entry = cls(**value)
        for identity in (entry.document_id, entry.session_id, entry.snapshot_id):
            if not isinstance(identity, str):
                raise ValueError("Invalid recovery identity")
            uuid.UUID(identity)
        if (type(entry.revision) is not int or entry.revision < 0
                or type(entry.saved_at) not in (int, float) or not math.isfinite(entry.saved_at)
                or not isinstance(entry.name, str) or not isinstance(entry.original_path, str)
                or entry.snapshot != entry.snapshot_id + ".etlayout"
                or entry.status not in {"recoverable", "deletion_failed", "legacy"}):
            raise ValueError("Invalid recovery receipt")
        entry.name.encode("utf-8")
        entry.original_path.encode("utf-8")
        try:
            datetime.fromtimestamp(entry.saved_at)
        except (ValueError, OverflowError, OSError) as error:
            raise ValueError("Invalid recovery timestamp") from error
        return entry


class RecoveryStore:
    """A window owns one random session; a document can have many sessions."""

    def __init__(self, root: str | Path, session_id: str | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.session_id = str(uuid.UUID(session_id)) if session_id else str(uuid.uuid4())
        self._lease = file_lock(self.root / (self.session_id + ".session"))
        self._lease.__enter__()
        self.last_error = ""
        self.issues = []

    def close(self):
        if self._lease is not None:
            self._lease.__exit__(None, None, None)
            self._lease = None

    def active(self, session_id: str) -> bool:
        if session_id == self.session_id and self._lease is not None:
            return True
        try:
            with file_lock(self.root / (session_id + ".session"), blocking=False):
                return False
        except OSError:
            return True

    def _read(self) -> dict[str, RecoveryEntry]:
        # Receipts are the independent journal: an interrupted/corrupt index is
        # rebuilt without trusting paths supplied by that index.
        entries = {}
        self.issues = []
        for index, receipt in enumerate(self.root.glob("*.receipt.json")):
            if index >= MAX_RECOVERY_ENTRIES:
                raise SimulationError("Recovery catalogue exceeds its entry budget")
            try:
                if receipt.is_symlink() or receipt.stat().st_size > 64_000:
                    continue
                entry = RecoveryEntry.decode(json.loads(receipt.read_text(encoding="utf-8")))
                if receipt.name != entry.snapshot_id + ".receipt.json":
                    continue
                snapshot = self.root / entry.snapshot
                if not snapshot.is_symlink() and snapshot.is_file():
                    entries[entry.snapshot_id] = entry
                else:
                    self.issues.append("Unavailable recovery snapshot " + entry.snapshot_id)
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                self.issues.append("Unreadable recovery receipt " + receipt.name)
                continue
        return entries

    def _publish(self, entries):
        payload = {"format": 1, "entries": [asdict(e) for e in entries.values()]}
        if len(json.dumps(payload).encode()) > MAX_INDEX_BYTES:
            raise SimulationError("Recovery catalogue exceeds its byte budget")
        atomic_json(self.root / "index.json", payload)

    def entries(self, *, include_active=False) -> list[RecoveryEntry]:
        with file_lock(self.root / "index.lock"):
            entries = self._read()
            try:
                self._publish(entries)
                self.last_error = ""
            except OSError:
                self.last_error = "Recovery index could not be updated; independent snapshots remain available."
        # Older snapshots remain available after a failed catalogue update, but
        # offer only the latest successful snapshot of each document/session.
        latest = {}
        for entry in entries.values():
            key = (entry.document_id, entry.session_id)
            if key not in latest or (entry.revision, entry.saved_at) > (latest[key].revision, latest[key].saved_at):
                latest[key] = entry
        return sorted((e for e in latest.values() if include_active or not self.active(e.session_id)),
                      key=lambda e: e.saved_at, reverse=True)

    def snapshot_path(self, entry: RecoveryEntry) -> Path:
        return self.root / RecoveryEntry.decode(asdict(entry)).snapshot

    def snapshot(self, project, result=None, history=None, *, revision: int, original_path=None,
                 writer=save_project) -> RecoveryEntry:
        identity = str(uuid.uuid4())
        entry = RecoveryEntry(document_id(project), self.session_id, identity, revision, project.name,
                              str(original_path or ""), identity + ".etlayout", time.time())
        RecoveryEntry.decode(asdict(entry))
        if len(json.dumps(asdict(entry)).encode("utf-8")) > 64_000:
            raise SimulationError("Recovery metadata exceeds its receipt budget. Save the project explicitly.")
        writer(project, self.root / entry.snapshot, result, history=history)
        entry = replace(entry, saved_at=time.time())
        # The archive remains valid even if receipt/index publication fails.
        with file_lock(self.root / "index.lock"):
            atomic_json(self.root / (identity + ".receipt.json"), asdict(entry))
            entries = self._read()
            self._publish(entries)
        return entry

    def cleanup(self, identity: str, revision: int, *, session_id=None) -> list[str]:
        """Remove this owner's snapshots only through the durable save revision."""
        owner = session_id or self.session_id
        if owner != self.session_id:
            raise SimulationError("Cannot clean another live session's recovery data")
        return self._remove(lambda e: e.document_id == identity and e.session_id == owner and e.revision <= revision)

    def discard(self, entry: RecoveryEntry) -> list[str]:
        """Explicit UI discard of a selected inactive snapshot, never a live owner."""
        if self.active(entry.session_id):
            raise SimulationError("Recovery snapshot is owned by an active application")
        return self._remove(lambda e: e.document_id == entry.document_id and e.session_id == entry.session_id
                            and e.revision <= entry.revision)

    def _remove(self, predicate) -> list[str]:
        errors = []
        with file_lock(self.root / "index.lock"):
            entries = self._read()
            for key, entry in list(entries.items()):
                if not predicate(entry):
                    continue
                try:
                    (self.root / entry.snapshot).unlink(missing_ok=True)
                    (self.root / (entry.snapshot_id + ".receipt.json")).unlink(missing_ok=True)
                    del entries[key]
                except OSError as error:
                    errors.append(str(error))
                    entries[key] = replace(entry, status="deletion_failed")
                    atomic_json(self.root / (entry.snapshot_id + ".receipt.json"), asdict(entries[key]))
            self._publish(entries)
        return errors

    def import_legacy(self, path: Path):
        """Register the prior shared recovery file without loading its resources."""
        if path.is_symlink() or not path.is_file() or (self.root / "legacy-imported.json").exists():
            return
        snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, path.absolute().as_uri()))
        with file_lock(self.root / "index.lock"):
            receipt = self.root / (snapshot_id + ".receipt.json")
            if receipt.exists():
                try:
                    if receipt.is_symlink() or receipt.stat().st_size > 64_000:
                        raise ValueError("Invalid legacy receipt")
                    entry = RecoveryEntry.decode(json.loads(receipt.read_text(encoding="utf-8")))
                    if not self.snapshot_path(entry).is_file():
                        raise ValueError("Legacy snapshot missing")
                    # A prior process may have stopped after receipt publication
                    # but before its marker. Acknowledge it before UI discard.
                    atomic_json(self.root / "legacy-imported.json", {"imported": True})
                    return
                except (ValueError, TypeError, KeyError):
                    pass
            import shutil
            target = self.root / (snapshot_id + ".etlayout")
            # Copy only the old local archive; never execute/open its resources.
            shutil.copyfile(path, target)
            entry = RecoveryEntry(snapshot_id, str(uuid.UUID(int=0)), snapshot_id, 0,
                                  "Legacy recovery", "", target.name, path.stat().st_mtime, "legacy")
            atomic_json(self.root / (snapshot_id + ".receipt.json"), asdict(entry))
            self._publish(self._read())
            atomic_json(self.root / "legacy-imported.json", {"imported": True})
