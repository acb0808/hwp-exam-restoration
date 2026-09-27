"""Process-aware file leases for the single Hancom automation session.

The HWP toolkit can control a desktop application, so a process-local mutex is
not sufficient: two Python processes must also observe the same lease.  This
module uses an exclusive lock-file create and records enough ownership data to
make stale-lock recovery conservative.  A lock is removed only by its owner,
or after the recorded owner process has been proven to be dead.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


LOCK_SCHEMA_VERSION = "1.0"


class LockBusyError(RuntimeError):
    """Raised when another live or unknown process owns a lease."""

    def __init__(self, path: Path, owner: dict[str, Any] | None = None) -> None:
        self.path = path
        self.owner = owner or {}
        run_id = self.owner.get("run_id") or "unknown"
        pid = self.owner.get("pid") or "unknown"
        super().__init__(f"HWP session lock is busy at {path} (run_id={run_id}, pid={pid})")


class LockOwnershipError(RuntimeError):
    """Raised when a caller tries to release a lease it does not own."""


def _process_is_alive(pid: Any) -> bool | None:
    """Return True/False, or None when the OS will not reveal process state.

    ``None`` is intentionally treated as busy by recovery.  Access denied must
    never become an excuse to remove a potentially active user's lock.
    """

    try:
        process_id = int(pid)
    except (TypeError, ValueError):
        # A malformed owner record is not evidence that its process is dead.
        # Recovery must leave the lease busy until a human or a later run can
        # establish ownership safely.
        return None
    if process_id <= 0:
        return None
    if process_id == os.getpid():
        return True
    # psutil gives a reliable distinction on Windows.  If it is unavailable,
    # the platform-specific read-only probe below is used instead.
    try:
        import psutil  # type: ignore

        process = psutil.Process(process_id)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        return True
    except ImportError:
        pass
    except Exception as exc:
        no_such = getattr(psutil, "NoSuchProcess", ())
        denied = getattr(psutil, "AccessDenied", ())
        if no_such and isinstance(exc, no_such):
            return False
        if denied and isinstance(exc, denied):
            return None
    if sys.platform == "win32":
        # ``os.kill(pid, 0)`` is not a process-state probe on Windows.  The
        # Windows implementation of os.kill uses TerminateProcess for most
        # signals, and its result does not provide the conservative
        # access-denied/unknown distinction needed for stale-lock recovery.
        return _windows_process_is_alive(process_id)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError as exc:
        if getattr(exc, "winerror", None) in {5, 32} or exc.errno in {errno.EPERM, errno.EACCES}:
            return None
        # Windows reports an out-of-range/nonexistent PID as ERROR_INVALID_PARAMETER
        # rather than raising ProcessLookupError.
        if getattr(exc, "winerror", None) == 87:
            return False
        if exc.errno == errno.ESRCH:
            return False
        return None
    return True


def _windows_process_is_alive(process_id: int) -> bool | None:
    """Probe a Windows PID without sending a signal or opening it for write.

    ``None`` means that the process state could not be proven.  Lock recovery
    treats that result as busy, which is safer for protected or inaccessible
    processes than guessing that the owner is dead.
    """

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # PROCESS_QUERY_LIMITED_INFORMATION is read-only and is sufficient for
        # GetExitCodeProcess on supported Windows versions.
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        handle = open_process(0x1000, False, process_id)
        if not handle:
            error = ctypes.get_last_error()
            # ERROR_INVALID_PARAMETER means the PID no longer exists.  Access
            # denied and every other failure remain unknown/busy.
            return False if error == 87 else None

        try:
            exit_code = wintypes.DWORD()
            get_exit_code = kernel32.GetExitCodeProcess
            get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            get_exit_code.restype = wintypes.BOOL
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return None
            # STILL_ACTIVE is the documented value for a running process.
            return exit_code.value == 259
        finally:
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            close_handle(handle)
    except Exception:
        # Missing ctypes bindings, unusual API failures, and access failures
        # all become unknown so recovery never unlinks a live user's lock.
        return None


def _process_name() -> str:
    try:
        return Path(sys.executable).name
    except Exception:
        return "python"


@dataclass(frozen=True)
class SessionOwner:
    schema_version: str
    run_id: str
    pid: int
    process_name: str
    document_key: str
    host: str
    acquired_at: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "pid": self.pid,
            "process_name": self.process_name,
            "document_key": self.document_key,
            "host": self.host,
            "acquired_at": self.acquired_at,
        }


class AppSessionLock:
    """An exclusive, process-aware lock for all HWP application operations.

    Parameters are deliberately explicit.  ``document_key`` is metadata, not
    a way to create independent app sessions: the single lock path serializes
    every document operation in this process/workspace.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        run_id: str,
        document_key: str,
        pid: int | None = None,
        process_name: str | None = None,
        acquired_at: str | None = None,
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(document_key, str) or not document_key.strip():
            raise ValueError("document_key must be a non-empty string")
        self.path = Path(path).expanduser()
        self.run_id = run_id
        self.document_key = document_key
        self.pid = int(pid if pid is not None else os.getpid())
        self.process_name = process_name or _process_name()
        self.acquired_at = acquired_at or _utc_now()
        self._owner: SessionOwner | None = None

    @property
    def acquired(self) -> bool:
        return self._owner is not None

    @property
    def owner(self) -> dict[str, Any]:
        if self._owner is not None:
            return self._owner.to_mapping()
        return _read_owner(self.path) or {}

    def acquire(self, *, recover_stale: bool = True) -> "AppSessionLock":
        """Acquire the lease atomically, optionally recovering a dead owner.

        Recovery is intentionally bounded to one attempt.  If another process
        wins a race after a stale file is removed, the normal exclusive create
        path reports contention instead of deleting again.
        """

        if self._owner is not None:
            raise LockOwnershipError(f"Lock already acquired by this lease: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        owner = SessionOwner(
            schema_version=LOCK_SCHEMA_VERSION,
            run_id=self.run_id,
            pid=self.pid,
            process_name=self.process_name,
            document_key=self.document_key,
            host=socket.gethostname(),
            acquired_at=self.acquired_at,
        )
        encoded = (json.dumps(owner.to_mapping(), ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )

        for attempt in range(2):
            try:
                fd = os.open(
                    str(self.path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                existing_bytes = _read_bytes(self.path)
                existing_owner = _decode_owner(existing_bytes)
                if recover_stale and attempt == 0 and _owner_is_provably_dead(existing_owner):
                    # Compare immediately before unlinking.  This does not
                    # claim to provide a kernel compare-and-delete primitive,
                    # but avoids deleting a file that visibly changed while
                    # the process was being inspected.
                    if _read_bytes(self.path) == existing_bytes:
                        try:
                            self.path.unlink()
                        except FileNotFoundError:
                            pass
                        continue
                raise LockBusyError(self.path, existing_owner)
            except OSError as exc:
                raise LockBusyError(self.path, _read_owner(self.path)) from exc
            else:
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                except Exception:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    raise
                self._owner = owner
                return self

        raise LockBusyError(self.path, _read_owner(self.path))

    def release(self) -> None:
        """Release only this lease's exact lock file."""

        if self._owner is None:
            return
        expected = self._owner.to_mapping()
        actual_bytes = _read_bytes(self.path)
        actual = _decode_owner(actual_bytes)
        if actual != expected:
            raise LockOwnershipError(
                f"Cannot release lock owned by another process or run: {self.path}"
            )
        try:
            self.path.unlink()
        except FileNotFoundError:
            # A missing file means an external actor already recovered it.  Do
            # not remove any replacement file and clear our local state.
            pass
        self._owner = None

    def recover(self) -> bool:
        """Remove a lock only when its recorded PID is proven dead."""

        current = _read_bytes(self.path)
        owner = _decode_owner(current)
        if not owner or not _owner_is_provably_dead(owner):
            return False
        if _read_bytes(self.path) != current:
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True

    def __enter__(self) -> "AppSessionLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


# Names used by callers that distinguish the application lease from the
# document metadata.  They intentionally share the same implementation.
SessionLock = AppSessionLock
DocumentLock = AppSessionLock


def _utc_now() -> str:
    # ISO output is stable and does not require datetime parsing on recovery.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except (FileNotFoundError, OSError):
        return None


def _decode_owner(raw: bytes | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return value


def _read_owner(path: Path) -> dict[str, Any]:
    return _decode_owner(_read_bytes(path))


def _owner_is_provably_dead(owner: dict[str, Any]) -> bool:
    if not owner:
        return False
    # Reclaim only a complete owner record written by this implementation.  A
    # truncated or unknown-schema record is intentionally busy: a missing
    # field is not evidence that the owner process has exited.
    required = {
        "schema_version",
        "run_id",
        "pid",
        "process_name",
        "document_key",
        "host",
        "acquired_at",
    }
    if not required.issubset(owner):
        return False
    if owner.get("schema_version") != LOCK_SCHEMA_VERSION:
        return False
    string_fields = ("run_id", "process_name", "document_key", "host", "acquired_at")
    if any(
        not isinstance(owner.get(key), str) or not owner[key].strip()
        for key in string_fields
    ):
        return False
    # A PID has meaning only on the host that recorded it.  Foreign hosts are
    # always busy even when the numeric PID is not currently running here.
    if owner["host"] != socket.gethostname():
        return False
    if isinstance(owner["pid"], bool) or not isinstance(owner["pid"], int):
        return False
    result = _process_is_alive(owner["pid"])
    return result is False


__all__ = [
    "AppSessionLock",
    "DocumentLock",
    "LOCK_SCHEMA_VERSION",
    "LockBusyError",
    "LockOwnershipError",
    "SessionLock",
    "SessionOwner",
]
