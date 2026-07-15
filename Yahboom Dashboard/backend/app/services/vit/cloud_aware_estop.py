"""
Cloud-aware stop mode for the test bench.

Bottle detections send stop on the dashboard client, not e-stop. This module only stores which stop mode is active.
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("cloud_aware_estop")

STOP_MODE_CACHE = "cache_aware_offloading"
STOP_MODE_CLOUD = "cloud_aware"
STOP_MODES = frozenset({STOP_MODE_CACHE, STOP_MODE_CLOUD})
MODES_NEED_PI_SCRIPT = frozenset({STOP_MODE_CACHE})

_MIN_CONFIDENCE = float(
    os.getenv("CLOUD_AWARE_MIN_CONFIDENCE")
    or os.getenv("EDGE_AWARE_MIN_CONFIDENCE", "70.0")
)
_COOLDOWN_SEC = float(
    os.getenv("CLOUD_AWARE_ESTOP_COOLDOWN_SEC")
    or os.getenv("EDGE_AWARE_ESTOP_COOLDOWN_SEC", "5.0")
)


class CloudAwareEstopService:
    """Tracks the cache-aware / cloud stop mode for the test bench."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode = STOP_MODE_CLOUD

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    @property
    def cloud_aware_enabled(self) -> bool:
        """Dashboard VIT bottle polling is always on — cloud stop is always armed."""
        return True

    @property
    def needs_pi_cache_script(self) -> bool:
        return self.mode in MODES_NEED_PI_SCRIPT

    def set_mode(self, mode: str) -> str:
        if mode == "edge_aware":
            mode = STOP_MODE_CLOUD
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
                "cloud_aware_enabled": True,
                "needs_pi_cache_script": self._mode in MODES_NEED_PI_SCRIPT,
                "min_confidence": _MIN_CONFIDENCE,
                "cooldown_sec": _COOLDOWN_SEC,
            }

    def on_vit_results(
        self,
        results: list[tuple[str, float]],
        reference_match=None,
    ) -> None:
        """Stop is handled on the dashboard client via reference match polling."""
        return


cloud_aware_estop = CloudAwareEstopService()
