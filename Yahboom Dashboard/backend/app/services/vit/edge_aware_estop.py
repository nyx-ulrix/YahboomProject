"""
Edge-aware stop mode for the test bench.

Bottle detections send stop on the dashboard client, not e-stop. This module only stores which stop mode is active.
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("edge_aware_estop")

STOP_MODE_CACHE = "cache_aware_offloading"
STOP_MODE_HYBRID = "hybrid"
STOP_MODE_EDGE = "edge_aware"
STOP_MODES = frozenset({STOP_MODE_CACHE, STOP_MODE_HYBRID, STOP_MODE_EDGE})
MODES_NEED_PI_SCRIPT = frozenset({STOP_MODE_CACHE, STOP_MODE_HYBRID})

_MIN_CONFIDENCE = float(os.getenv("EDGE_AWARE_MIN_CONFIDENCE", "40.0"))
_COOLDOWN_SEC = float(os.getenv("EDGE_AWARE_ESTOP_COOLDOWN_SEC", "5.0"))


class EdgeAwareEstopService:
    """Tracks cache / hybrid / edge stop mode for the test bench."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode = STOP_MODE_EDGE

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    @property
    def edge_aware_enabled(self) -> bool:
        """Dashboard VIT bottle polling — on for edge and hybrid."""
        return self.mode in (STOP_MODE_EDGE, STOP_MODE_HYBRID)

    @property
    def needs_pi_cache_script(self) -> bool:
        return self.mode in MODES_NEED_PI_SCRIPT

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
                "edge_aware_enabled": self._mode in (STOP_MODE_EDGE, STOP_MODE_HYBRID),
                "needs_pi_cache_script": self._mode in MODES_NEED_PI_SCRIPT,
                "min_confidence": _MIN_CONFIDENCE,
                "cooldown_sec": _COOLDOWN_SEC,
            }

    def on_vit_results(self, results: list[tuple[str, float]]) -> None:
        """Stop-label stop is handled on the dashboard client via stop command."""
        return


edge_aware_estop = EdgeAwareEstopService()
