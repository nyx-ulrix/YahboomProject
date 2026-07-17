"""In-memory test-bench session — shared across dashboard browser instances."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("test_bench_session")

_STOP_MODES = frozenset({"cloud_aware", "cache_aware_offloading"})


class TestBenchSessionService:
    """Tracks one active mission-test session for cross-browser UI sync."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False
        self._origin: str | None = None
        self._command_sent_at_ms: float | None = None
        self._active_start_ms: float | None = None
        self._frozen_elapsed_ms: float | None = None
        self._session_start_wall_ms: float | None = None
        self._stop_mode: str | None = None
        self._completed_run: dict[str, Any] | None = None
        self._completed_at: float | None = None

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "active": self._active,
            "origin": self._origin,
            "command_sent_at_ms": self._command_sent_at_ms,
            "active_start_ms": self._active_start_ms,
            "frozen_elapsed_ms": self._frozen_elapsed_ms,
            "session_start_wall_ms": self._session_start_wall_ms,
            "stop_mode": self._stop_mode,
        }

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            payload = self._snapshot_unlocked()
            if self._completed_run is not None:
                payload["completed_run"] = self._completed_run
                payload["completed_at"] = self._completed_at
            return payload

    def start(
        self,
        *,
        origin: str,
        command_sent_at_ms: float,
        stop_mode: str,
        session_start_wall_ms: float,
    ) -> tuple[bool, dict[str, Any], str]:
        if stop_mode not in _STOP_MODES:
            return False, self.get_status(), "invalid_stop_mode"
        with self._lock:
            if self._active:
                return False, self._snapshot_unlocked(), "session_already_active"
            self._active = True
            self._origin = origin
            self._command_sent_at_ms = float(command_sent_at_ms)
            self._active_start_ms = None
            self._frozen_elapsed_ms = None
            self._session_start_wall_ms = float(session_start_wall_ms)
            self._stop_mode = stop_mode
            self._completed_run = None
            self._completed_at = None
            log.info(
                "Test bench session started (origin=%s mode=%s cmd_ms=%.0f)",
                origin,
                stop_mode,
                command_sent_at_ms,
            )
            return True, self._snapshot_unlocked(), "ok"

    def update(
        self,
        *,
        active_start_ms: float | None = None,
        frozen_elapsed_ms: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if not self._active:
                return self._snapshot_unlocked()
            if active_start_ms is not None:
                self._active_start_ms = float(active_start_ms)
            if frozen_elapsed_ms is not None:
                self._frozen_elapsed_ms = float(frozen_elapsed_ms)
            return self._snapshot_unlocked()

    def clear(self) -> dict[str, Any]:
        with self._lock:
            if self._active:
                log.info("Test bench session cleared (origin=%s)", self._origin)
            self._active = False
            self._origin = None
            self._command_sent_at_ms = None
            self._active_start_ms = None
            self._frozen_elapsed_ms = None
            self._session_start_wall_ms = None
            self._stop_mode = None
            return self._snapshot_unlocked()

    def complete(self, run: dict[str, Any], origin: str) -> tuple[bool, dict[str, Any]]:
        """First completer records the run and clears the active session."""
        with self._lock:
            if not self._active:
                payload = self._snapshot_unlocked()
                if self._completed_run is not None:
                    payload["completed_run"] = self._completed_run
                    payload["recorded"] = False
                else:
                    payload["recorded"] = False
                return False, payload

            recorded_run = dict(run)
            recorded_run.setdefault("completed_by", origin)
            self._completed_run = recorded_run
            self._completed_at = time.time()
            self._active = False
            log.info(
                "Test bench session completed (origin=%s run=%s)",
                origin,
                recorded_run.get("run"),
            )
            payload = self._snapshot_unlocked()
            payload["completed_run"] = recorded_run
            payload["recorded"] = True
            return True, payload

    def take_completed_run(self) -> dict[str, Any] | None:
        with self._lock:
            run = self._completed_run
            self._completed_run = None
            self._completed_at = None
            return run


test_bench_session = TestBenchSessionService()
