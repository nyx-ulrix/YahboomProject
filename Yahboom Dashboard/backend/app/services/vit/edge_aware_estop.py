"""
Edge-aware stop mode for the test bench.

Bottle detections engage soft stop (`auto_soft_stop`) on the dashboard client, not e-stop. This module only stores which stop mode is active.
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("edge_aware_estop")

STOP_MODE_CACHE = "cache_aware_offloading"
STOP_MODE_EDGE = "edge_aware"
STOP_MODES = frozenset({STOP_MODE_CACHE, STOP_MODE_EDGE})

_MIN_CONFIDENCE = float(os.getenv("EDGE_AWARE_MIN_CONFIDENCE", "40.0"))
_COOLDOWN_SEC = float(os.getenv("EDGE_AWARE_ESTOP_COOLDOWN_SEC", "5.0"))


class EdgeAwareEstopService:
    """Tracks edge-aware vs cache-aware stop mode for the test bench."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode = STOP_MODE_EDGE

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    @property
    def edge_aware_enabled(self) -> bool:
        return self.mode == STOP_MODE_EDGE

    def set_mode(self, mode: str) -> str:
        if mode not in STOP_MODES:
            raise ValueError(f"mode must be one of {sorted(STOP_MODES)}")
        with self._lock:
            prev = self._mode
            self._mode = mode
        if prev != mode:
            log.info("Stop mode: %s -> %s", prev, mode)
        return mode

    def get_status(self) -> dict:
        with self._lock:
            return {
                "mode": self._mode,
                "edge_aware_enabled": self._mode == STOP_MODE_EDGE,
                "min_confidence": _MIN_CONFIDENCE,
                "cooldown_sec": _COOLDOWN_SEC,
            }

    def on_vit_results(self, results: list[tuple[str, float]]) -> None:
        """Stop-label soft stop is handled on the dashboard client via auto_soft_stop."""
        return


edge_aware_estop = EdgeAwareEstopService()
