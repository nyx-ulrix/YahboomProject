"""
Backhaul delay simulation.

Standalone service that reproduces the provided wired-backhaul delay model and
sleeps for a gamma-distributed delay on every non-video MQTT hop (send/receive).
The video feed relay is intentionally NOT wired to this service.

The core delay computation below is kept identical to the provided script so the
behaviour matches exactly; only config loading/persistence and thread-safety are
added around it.
"""

import json
import math
import re
import threading
import time
from pathlib import Path

import numpy as np

# backend/app/services/backhaul_delay.py -> backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _BACKEND_ROOT / "backhaul_config.json"


class BackhaulDelay:
    """Thread-safe holder for the backhaul config + the gamma delay model."""

    def __init__(self, config_path: Path = _CONFIG_PATH) -> None:
        self._config_path = config_path
        self._lock = threading.Lock()
        self._enabled = True
        self._cfg = self._load()

    # ── config ────────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        with open(self._config_path) as f:
            return json.load(f)

    def _persist_h(self, h: int) -> None:
        """
        Update only the "h" value in the file, leaving the rest of the JSON
        byte-identical so the original format (e.g. 1e-8) is preserved.
        """
        text = self._config_path.read_text()
        new_text, count = re.subn(
            r'("h"\s*:\s*)-?\d+', rf'\g<1>{int(h)}', text, count=1
        )
        if count == 0:
            # Fallback: rewrite from scratch if the key wasn't found.
            new_text = json.dumps(self._cfg, indent=4) + "\n"
        self._config_path.write_text(new_text)

    def get_config(self) -> dict:
        with self._lock:
            cfg = dict(self._cfg)
            enabled = self._enabled
        cfg["enabled"] = enabled
        cfg["sample_delay_ms"] = round(self._compute_delay_ms(cfg), 2)
        return cfg

    def set_h(self, h: int) -> dict:
        with self._lock:
            self._cfg["h"] = int(h)
            self._persist_h(int(h))
        return self.get_config()

    def set_enabled(self, enabled: bool) -> dict:
        with self._lock:
            self._enabled = bool(enabled)
        return self.get_config()

    # ── delay model (kept identical to the provided script) ─────────────────────

    @staticmethod
    def _compute_delay_ms(cfg: dict) -> float:
        shape = math.floor(
            (1 + 1.28 * cfg["M_BS"] / cfg["M_GW"]) * cfg["k1"]
            + (cfg["h"] - 1) * cfg["k2"]
        )

        scale = cfg["a"] + cfg["packet_size_bits"] * cfg["k3"]

        delay_ms = np.random.gamma(shape, scale) * 1000

        return delay_ms

    def apply(self) -> float:
        """
        Sleep for one sampled backhaul delay and return it in ms.

        No-op (returns 0.0) when disabled or if anything goes wrong, so the MQTT
        pipeline never breaks because of the simulator.
        """
        with self._lock:
            if not self._enabled:
                return 0.0
            cfg = dict(self._cfg)
        try:
            delay_ms = self._compute_delay_ms(cfg)
            time.sleep(delay_ms / 1000)
            return delay_ms
        except Exception:
            return 0.0


def format_hop_suffix(delay_ms: float) -> str:
    """Fragment appended to event-log MQTT lines when a hop delay was applied."""
    if delay_ms <= 0:
        return ""
    return f" ({delay_ms:.1f}ms hop)"


# Global singleton (used by the MQTT services and Flask routes).
backhaul_delay = BackhaulDelay()


if __name__ == "__main__":
    # Standalone behaviour mirrors the original script: sample once, print, sleep.
    # Reuses the single delay model defined above to avoid duplicating the formula.
    with open(_CONFIG_PATH) as f:
        cfg = json.load(f)

    delay_ms = BackhaulDelay._compute_delay_ms(cfg)

    print(f"Wired Backhaul Delay: {delay_ms:.2f} ms")

    time.sleep(delay_ms / 1000)
