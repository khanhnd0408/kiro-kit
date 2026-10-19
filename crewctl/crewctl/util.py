"""Small helpers: hashing, atomic writes, a lock file, time."""

import datetime
import hashlib
import json
import os
import socket
import tempfile
import time


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    """Return the SHA-256 of a file, or None if it does not exist."""
    try:
        with open(path, "rb") as handle:
            return sha256_bytes(handle.read())
    except FileNotFoundError:
        return None


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def pretty_json(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def atomic_write(path, data, mode=0o600):
    """Write bytes to path by renaming a temp file in the same folder over it."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".crewctl-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class LockError(RuntimeError):
    pass


class FileLock:
    """An exclusive lock file. A lock left by a dead process on this host is taken over."""

    def __init__(self, path, break_lock=False):
        self.path = path
        self.break_lock = break_lock
        self.held = False

    def _owner(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _alive(pid):
        if os.name == "nt":
            # os.kill(pid, 0) on Windows sends Ctrl+C to the process group instead of probing. Never
            # guess: treat the owner as alive, and let the user clear a stuck lock with --break-lock.
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        return True

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                owner = self._owner()
                same_host = owner.get("host") == socket.gethostname()
                stale = same_host and isinstance(owner.get("pid"), int) and not self._alive(owner["pid"])
                if self.break_lock or stale:
                    try:
                        os.unlink(self.path)
                    except FileNotFoundError:
                        pass
                    continue
                raise LockError(
                    f"state is locked by pid {owner.get('pid', '?')} on {owner.get('host', '?')} "
                    f"since {owner.get('since', '?')}. If that run is gone, use --break-lock."
                )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "host": socket.gethostname(), "since": now_iso(), "t": time.time()}, handle)
            self.held = True
            return self
        raise LockError("could not take the state lock")

    def release(self):
        if self.held:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
