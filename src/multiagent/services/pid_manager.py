"""
PID Manager — process ID file lifecycle.

Extracted from conductor.py for single-responsibility.
"""

import os
import signal
import logging
from pathlib import Path

_log = logging.getLogger("multiagent.pid")


class PidManager:
    """Manages PID file: write, check, cleanup, signal."""

    def __init__(self, pid_path: Path = None, logger=None):
        self.pid_path = pid_path or (Path.cwd() / ".conductor.pid")
        self.log = logger or _log

    def acquire(self) -> int:
        """Write PID file. Returns PID. Raises RuntimeError if already running."""
        pid = os.getpid()

        if self.pid_path.exists():
            try:
                old_pid = int(self.pid_path.read_text().strip())
                try:
                    os.kill(old_pid, 0)
                    raise RuntimeError(
                        f"Conductor already running (PID={old_pid}). "
                        f"Stop it first or remove {self.pid_path}"
                    )
                except ProcessLookupError:
                    self.log.warning("Stale PID file (PID=%d), overwriting", old_pid)
            except ValueError:
                self.log.warning("Corrupt PID file, overwriting")

        self.pid_path.write_text(str(pid))
        self.log.info("PID file: %s (PID=%d)", self.pid_path, pid)
        return pid

    def release(self):
        """Remove PID file."""
        if self.pid_path.exists():
            try:
                self.pid_path.unlink()
                self.log.info("PID file removed: %s", self.pid_path)
            except OSError:
                pass

    def stop_by_pid(self, pid: int, timeout: float = 5.0) -> bool:
        """Send SIGTERM, wait for exit, force SIGKILL on timeout."""
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True

        import time
        for _ in range(int(timeout * 2)):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                self.release()
                return True

        # Force kill
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.release()
        return True
