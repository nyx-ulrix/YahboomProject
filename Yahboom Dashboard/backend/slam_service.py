"""
SLAM Service — 2-D LiDAR occupancy-grid mapping.

Subscribes to MQTT scan/grid topics (default yahboom/scan, yahboom/grid),
updates slam_map.json using a Cartographer-inspired correlative scan matcher,
and serves map state to the API.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# ── bootstrap path so this file can be run standalone ────────────────────────
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

load_dotenv(dotenv_path=_HERE.parent / ".env")

# ── Logging ───────────────────────────────────────────────────────────────────
_log_level = os.getenv("SLAM_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s [SLAM] %(levelname)s  %(message)s",
)
log = logging.getLogger("slam")

# ── MQTT / broker ─────────────────────────────────────────────────────────────
_BROKER_PORT     = int(os.getenv("MQTT_BROKER_PORT",   "1883"))
_SCAN_TOPIC      = os.getenv("SLAM_SCAN_TOPIC",        "yahboom/scan")
_GRID_TOPIC      = os.getenv("MQTT_GRID_TOPIC",        "yahboom/grid")
_CMD_TOPIC       = os.getenv("MQTT_TOPIC",             "yahboom/cmd")
_MQTT_TIMEOUT    = int(os.getenv("MQTT_TIMEOUT",       "60"))

# ── Map parameters ────────────────────────────────────────────────────────────
_MAP_SIZE_M      = float(os.getenv("SLAM_MAP_SIZE_M",    "20.0"))  # total metres
_RESOLUTION      = float(os.getenv("SLAM_RESOLUTION_M",  "0.05"))  # m / cell
_MAX_RANGE_M     = float(os.getenv("SLAM_MAX_RANGE_M",   "8.0"))
_MIN_RANGE_M     = float(os.getenv("SLAM_MIN_RANGE_M",   "0.05"))

# ── Cartographer-style SLAM tuning ────────────────────────────────────────────
_SEARCH_RADIUS_M = float(os.getenv("SLAM_SEARCH_RADIUS_M", "0.70"))
_SEARCH_ANGLE    = float(os.getenv("SLAM_SEARCH_ANGLE",    "0.50"))  # rad ≈ 29°
_BLUR_SIGMA_M    = float(os.getenv("SLAM_BLUR_SIGMA_M",    "0.10"))  # metres
_LOG_OCC         = float(os.getenv("SLAM_LOG_OCC",         "0.60"))
_LOG_FREE        = float(os.getenv("SLAM_LOG_FREE",        "0.30"))
_LOG_MAX         = float(os.getenv("SLAM_LOG_MAX",         "5.00"))
_OCC_LOCK        = float(os.getenv("SLAM_OCC_LOCK",        "1.25"))
_MIN_CONF        = float(os.getenv("SLAM_MIN_CONFIDENCE",  "0.05"))
_MAX_TRAJ        = int(os.getenv("SLAM_MAX_TRAJECTORY",    "2000"))
_WRITE_INTERVAL  = float(os.getenv("SLAM_WRITE_INTERVAL_S","0.50"))
_OUTPUT_PATH     = Path(os.getenv("SLAM_OUTPUT_FILE",
                         str(_HERE / "slam_map.json")))
# Max scan points processed per update (downsampled for performance)
_MAX_SCAN_PTS    = int(os.getenv("SLAM_MAX_SCAN_PTS",      "720"))

# Auto-resize map (keeps memory; expands grid as robot explores)
_AUTO_RESIZE_MAP = os.getenv("SLAM_AUTO_RESIZE", "true").lower() in ("true", "1", "yes", "on")
_RESIZE_MARGIN_M = float(os.getenv("SLAM_RESIZE_MARGIN_M", "1.5"))  # metres of headroom
_RESIZE_STEP_M   = float(os.getenv("SLAM_RESIZE_STEP_M",   "5.0"))  # grow in metre chunks

# Simple motion model (dead-reckoning from commands) so scans can be stitched
# across a whole room even without wheel odometry.
_LIN_MPS         = float(os.getenv("SLAM_LINEAR_MPS",   "0.25"))  # metres / second
_ANG_RPS         = float(os.getenv("SLAM_ANGULAR_RPS",  "1.1"))   # radians / second

# LiDAR-only motion fallback. This lets mapping continue when the robot moves
# autonomously and the backend does not receive exact wheel odometry.
_ICP_ENABLED     = os.getenv("SLAM_ICP_ENABLED", "true").lower() in ("true", "1", "yes", "on")
_ICP_MAX_PTS     = int(os.getenv("SLAM_ICP_MAX_PTS", "240"))
_ICP_ITERS       = int(os.getenv("SLAM_ICP_ITERS", "8"))
_ICP_MAX_PAIR_M  = float(os.getenv("SLAM_ICP_MAX_PAIR_M", "0.35"))
_ICP_MIN_PAIRS   = int(os.getenv("SLAM_ICP_MIN_PAIRS", "20"))
_ICP_MIN_CONF    = float(os.getenv("SLAM_ICP_MIN_CONFIDENCE", "0.25"))
_ICP_MIN_CONF_NO_CMD = float(os.getenv("SLAM_ICP_MIN_CONF_NO_CMD", "0.12"))
_ICP_MAX_STEP_M  = float(os.getenv("SLAM_ICP_MAX_STEP_M", "0.80"))
_ICP_MAX_ROT_RAD = float(os.getenv("SLAM_ICP_MAX_ROT_RAD", "0.70"))

# CSM guard — prevent map matching from snapping pose back to the origin blob
_CSM_MAX_CORRECTION_M = float(os.getenv("SLAM_CSM_MAX_CORRECTION_M", "0.40"))
_CSM_REFINE_MAX_M     = float(os.getenv("SLAM_CSM_REFINE_MAX_M", "0.25"))
_CSM_HIGH_CONF        = float(os.getenv("SLAM_CSM_HIGH_CONF", "0.35"))

# Turn-scan calibration — polar bearing correlation while rotating
_TURN_CALIB_ENABLED  = os.getenv("SLAM_TURN_CALIB_ENABLED", "true").lower() in ("true", "1", "yes", "on")
_TURN_BEARING_BINS   = int(os.getenv("SLAM_TURN_BEARING_BINS", "360"))
_TURN_MAX_SEARCH_RAD = float(os.getenv("SLAM_TURN_MAX_SEARCH_RAD", "1.20"))
_TURN_RANGE_TOL_M    = float(os.getenv("SLAM_TURN_RANGE_TOL_M", "0.30"))
_TURN_MIN_BINS       = int(os.getenv("SLAM_TURN_MIN_BINS", "25"))
_TURN_MIN_CONF       = float(os.getenv("SLAM_TURN_MIN_CONF", "0.22"))
_TURN_MIN_DELTA_RAD  = float(os.getenv("SLAM_TURN_MIN_DELTA_RAD", "0.015"))
_TURN_HINT_MAX_ERR_RAD = float(os.getenv("SLAM_TURN_HINT_MAX_ERR_RAD", "0.55"))
_TURN_CALIB_ALPHA    = float(os.getenv("SLAM_TURN_CALIB_ALPHA", "0.12"))
_TURN_SEARCH_ANGLE   = float(os.getenv("SLAM_TURN_SEARCH_ANGLE", "0.90"))
_TURN_ICP_MAX_ROT_RAD = float(os.getenv("SLAM_TURN_ICP_MAX_ROT_RAD", "1.20"))

_TURN_COMMANDS = frozenset({"left", "right", "fwdleft", "fwdright", "bckleft", "bckright"})
_PURE_TURN_COMMANDS = frozenset({"left", "right"})
_MOTION_COMMANDS = frozenset({
    "fwd", "bck", "left", "right", "fwdleft", "fwdright", "bckleft", "bckright",
})
_NON_MOTION_COMMANDS = frozenset({"stop", "auto_on", "auto_off", "estop_on", "estop_off"})

_VIEW_PADDING_M  = float(os.getenv("SLAM_VIEW_PADDING_M", "1.5"))
_VIEW_MIN_SIZE_M = float(os.getenv("SLAM_VIEW_MIN_SIZE_M", "5.0"))


# ═════════════════════════════════════════════════════════════════════════════
# Utility
# ═════════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _world_to_cell(x: float, y: float,
                   ox: float, oy: float, res: float,
                   rows: int, cols: int) -> Optional[tuple[int, int]]:
    """World (x, y) → (row, col).  Returns None if outside map."""
    c = int((x - ox) / res)
    r = int((y - oy) / res)
    if 0 <= r < rows and 0 <= c < cols:
        return r, c
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Cartographer-Inspired SLAM Core
# ═════════════════════════════════════════════════════════════════════════════

class CartographerSLAM:
    """
    2-D occupancy-grid SLAM using Google Cartographer's core approach:

    1. Correlative Scan Matcher (CSM)  –  FFT cross-correlation over a
       discrete rotation set to find the best (dx, dy, dθ) displacement.
    2. Gaussian-blurred probability map  –  creates the "gravitational well"
       around obstacles so the scan can lock onto the map from a distance.
    3. Log-odds Bresenham ray casting  –  marks free space along each ray and
       the endpoint as occupied (standard Bayesian update).

    Map convention
    --------------
    • Cell (row, col) → world (x, y):
        x = origin_x + col * resolution
        y = origin_y + row * resolution   (row 0 = southernmost row = min-y)
    • Robot pose: (x m, y m, theta rad)  –  theta=0 is +X (east), CCW +.
    """

    def __init__(self) -> None:
        self.resolution  = _RESOLUTION
        self.n           = int(_MAP_SIZE_M / _RESOLUTION)   # cells per side
        half             = _MAP_SIZE_M / 2.0
        self.origin_x    = -half
        self.origin_y    = -half

        # Log-odds grid: 0 = unknown, >0 = occupied, <0 = free
        self._log: np.ndarray = np.zeros((self.n, self.n), dtype=np.float32)

        # Gaussian-blurred probability map for CSM (Cartographer-style)
        self._blur_sigma  = _BLUR_SIGMA_M / _RESOLUTION     # in cells
        self._prob_blur: np.ndarray = np.zeros_like(self._log)

        self.pose          = np.zeros(3, dtype=np.float64)  # x, y, theta
        self._last_pts: Optional[np.ndarray] = None
        self._last_pose: Optional[np.ndarray] = None
        self._last_world_pts: Optional[np.ndarray] = None
        self.trajectory: list[dict] = []
        self._scans        = 0
        self._confidence   = 1.0
        self._icp_confidence = 0.0
        self._rejected_scans = 0
        self._lock         = threading.Lock()
        self._dirty        = False

        self._resize_step_cells = max(1, int(round(_RESIZE_STEP_M / self.resolution)))
        self._last_motion_t = time.time()
        self._last_scan_t: float | None = None
        self._active_cmd: str | None = None

        # Online turn calibration (LiDAR-measured vs command-integrated rotation)
        self._ang_rps_eff = _ANG_RPS
        self._turn_cal_confidence = 0.0
        self._turn_cal_samples = 0
        self._last_turn_dtheta: float | None = None

    # ── Public scan ingestion ─────────────────────────────────────────────────

    def process_raw_scan(self, msg: dict) -> bool:
        """
        Process a raw LaserScan payload:
          { angle_min, angle_max, angle_increment, ranges: [] }
        """
        try:
            a0  = float(msg["angle_min"])
            da  = float(msg["angle_increment"])
            rng = np.asarray(msg["ranges"], dtype=np.float64)
        except (KeyError, ValueError, TypeError):
            log.warning("Malformed LaserScan – skipped.")
            return False

        angles = a0 + np.arange(len(rng)) * da
        ok     = (rng >= _MIN_RANGE_M) & (rng <= _MAX_RANGE_M) & np.isfinite(rng)
        angles, rng = angles[ok], rng[ok]

        if len(rng) < 10:
            return False

        pts = np.column_stack([rng * np.cos(angles), rng * np.sin(angles)])
        return self._update(pts)

    def process_grid_scan(self, msg: dict) -> bool:
        """
        Reconstruct a local-frame point cloud from an occupancy-grid message.
        Occupancy grids published by the robot are robot-centric (robot at centre).
        """
        try:
            w    = int(msg.get("w") or msg.get("width") or 120)
            h    = int(msg.get("h") or msg.get("height") or 120)
            flat = (msg.get("grid") or msg.get("cells")
                    or msg.get("data") or msg.get("occupancy"))
            if flat is None:
                return False
            grid = np.asarray(flat, dtype=np.int8).reshape((h, w))
        except (ValueError, TypeError):
            log.warning("Malformed grid message – skipped.")
            return False

        grid_res = float(msg.get("resolution") or self.resolution)
        cx, cy   = w // 2, h // 2

        occ = np.argwhere(grid == 1)
        if len(occ) == 0:
            return False

        # (row, col) → local (x, y):  col right = +x,  row up = +y (flip rows)
        lx = (occ[:, 1] - cx) * grid_res
        ly = (cy - occ[:, 0]) * grid_res

        d    = np.hypot(lx, ly)
        mask = (d >= _MIN_RANGE_M) & (d <= _MAX_RANGE_M)
        if mask.sum() < 10:
            return False

        pts = np.column_stack([lx[mask], ly[mask]])
        return self._update(pts)

    # ── Core SLAM update ──────────────────────────────────────────────────────

    def _update(self, local_pts: np.ndarray) -> bool:
        """Thread-safe: match, update map, record trajectory."""
        # Downsample for performance
        if len(local_pts) > _MAX_SCAN_PTS:
            idx      = np.round(np.linspace(0, len(local_pts) - 1,
                                            _MAX_SCAN_PTS)).astype(int)
            local_pts = local_pts[idx]

        with self._lock:
            scan_t = time.time()
            # Integrate commanded motion up to this scan timestamp (dead-reckoning).
            self._integrate_motion(scan_t)
            predicted_pose = self.pose.copy()
            used_icp = False
            used_turn_cal = False
            turning = self._is_turn_command()

            if self._last_pts is not None and len(self._last_pts) >= 10:
                hint_dtheta = self._normalize_angle(
                    predicted_pose[2] - float(self._last_pose[2])
                )
                has_cmd_motion = self._is_motion_command()
                icp_min_conf = _ICP_MIN_CONF if has_cmd_motion else _ICP_MIN_CONF_NO_CMD
                icp_max_rot = _TURN_ICP_MAX_ROT_RAD if turning else _ICP_MAX_ROT_RAD

                icp_pose, icp_conf = self._estimate_scan_motion(
                    local_pts, max_rot_rad=icp_max_rot,
                )
                self._icp_confidence = icp_conf
                if icp_pose is not None and icp_conf >= icp_min_conf:
                    self.pose = icp_pose
                    used_icp = True
                elif self._has_motion_hint(predicted_pose):
                    self.pose = predicted_pose.copy()
                elif icp_pose is not None and icp_conf >= icp_min_conf * 0.5:
                    # Weak LiDAR motion is still better than staying frozen when
                    # there are no dashboard/MQTT drive commands (e.g. ROS auto).
                    self.pose = icp_pose
                    used_icp = True

                if turning and _TURN_CALIB_ENABLED:
                    turn_dtheta, turn_conf = self._estimate_turn_rotation(
                        self._last_pts, local_pts, hint_dtheta=hint_dtheta,
                    )
                    self._turn_cal_confidence = turn_conf
                    if self._accept_turn_calibration(turn_dtheta, turn_conf, hint_dtheta):
                        assert turn_dtheta is not None
                        new_theta = float(self._last_pose[2]) + turn_dtheta
                        if self._is_pure_turn_command():
                            self.pose = np.array([
                                float(self._last_pose[0]),
                                float(self._last_pose[1]),
                                new_theta,
                            ], dtype=np.float64)
                        else:
                            self.pose = np.array([
                                float(self.pose[0]),
                                float(self.pose[1]),
                                new_theta,
                            ], dtype=np.float64)
                        used_turn_cal = True
                        self._last_turn_dtheta = turn_dtheta
                        self._update_angular_calibration(
                            hint_dtheta, turn_dtheta, turn_conf,
                        )
                        log.debug(
                            "Turn cal  conf=%.3f  dtheta=%.1f°  ang_rps=%.3f",
                            turn_conf,
                            math.degrees(turn_dtheta),
                            self._ang_rps_eff,
                        )

                pose_before_csm = self.pose.copy()
                search_angle = _TURN_SEARCH_ANGLE if turning else _SEARCH_ANGLE
                world_pts = self._scan_match(local_pts, search_angle=search_angle)
                self._apply_csm_guard(
                    pose_before_csm, predicted_pose, used_icp, used_turn_cal,
                )
            else:
                world_pts = self._transform(local_pts, self.pose)
                self._confidence = 1.0

            if (self._last_pts is not None
                    and self._confidence < _MIN_CONF
                    and not used_icp
                    and not used_turn_cal
                    and not self._has_motion_hint(predicted_pose)):
                self.pose = predicted_pose
                self._rejected_scans += 1
                self._dirty = True
                log.debug(
                    "Rejected scan: no reliable pose update "
                    "(map_conf=%.3f, icp_conf=%.3f).",
                    self._confidence,
                    self._icp_confidence,
                )
                return False

            if _AUTO_RESIZE_MAP:
                self._ensure_bounds(world_pts)

            self._ray_cast_update(world_pts)
            self._refresh_blur()
            self._last_pts = local_pts
            self._last_pose = self.pose.copy()
            self._last_world_pts = world_pts
            self._scans   += 1
            self._dirty    = True

            self.trajectory.append({
                "x":     round(float(self.pose[0]), 4),
                "y":     round(float(self.pose[1]), 4),
                "theta": round(float(self.pose[2]), 4),
                "t":     _now_iso(),
            })
            if len(self.trajectory) > _MAX_TRAJ:
                self.trajectory = self.trajectory[-_MAX_TRAJ:]

            self._last_scan_t = scan_t

        return True

    def apply_command(self, cmd: str) -> None:
        """Set the current motion command used for dead-reckoning."""
        with self._lock:
            self._integrate_motion(time.time())
            self._active_cmd = (cmd or "").strip() or None

    def _integrate_motion(self, now: float) -> None:
        """Unicycle dead-reckoning based on the last commanded motion."""
        dt = max(0.0, float(now - self._last_motion_t))
        self._last_motion_t = now
        if dt <= 0 or not self._active_cmd:
            return

        cmd = self._active_cmd
        if cmd == "stop":
            return

        v = 0.0
        w = 0.0

        if cmd in {"fwd", "fwdleft", "fwdright"}:
            v = _LIN_MPS
        elif cmd in {"bck", "bckleft", "bckright"}:
            v = -_LIN_MPS

        if cmd in {"left", "fwdleft", "bckleft"}:
            w = self._ang_rps_eff
        elif cmd in {"right", "fwdright", "bckright"}:
            w = -self._ang_rps_eff

        # Integrate
        theta = float(self.pose[2])
        theta = theta + w * dt
        self.pose[2] = theta
        self.pose[0] = float(self.pose[0]) + math.cos(theta) * v * dt
        self.pose[1] = float(self.pose[1]) + math.sin(theta) * v * dt

    def _has_motion_hint(self, predicted_pose: np.ndarray) -> bool:
        """Return true if command dead-reckoning moved the pose this scan."""
        if self._last_pose is None:
            return True
        moved = float(np.linalg.norm(predicted_pose[:2] - self._last_pose[:2]))
        turned = abs(float(predicted_pose[2] - self._last_pose[2]))
        return moved > 0.01 or turned > 0.02

    @staticmethod
    def _normalize_angle(theta: float) -> float:
        """Wrap angle to (-pi, pi]."""
        return math.atan2(math.sin(theta), math.cos(theta))

    def _is_turn_command(self) -> bool:
        return (self._active_cmd or "") in _TURN_COMMANDS

    def _is_pure_turn_command(self) -> bool:
        return (self._active_cmd or "") in _PURE_TURN_COMMANDS

    def _is_motion_command(self) -> bool:
        cmd = (self._active_cmd or "").strip()
        if not cmd or cmd in _NON_MOTION_COMMANDS:
            return False
        return cmd in _MOTION_COMMANDS

    def _accept_turn_calibration(
        self,
        dtheta: Optional[float],
        conf: float,
        hint_dtheta: float,
    ) -> bool:
        """Reject no-op or inconsistent turn matches that block ICP updates."""
        if dtheta is None or conf < _TURN_MIN_CONF:
            return False
        if abs(dtheta) < _TURN_MIN_DELTA_RAD:
            return False
        if abs(hint_dtheta) >= _TURN_MIN_DELTA_RAD:
            if dtheta * hint_dtheta <= 0:
                return False
            if abs(self._normalize_angle(dtheta - hint_dtheta)) > _TURN_HINT_MAX_ERR_RAD:
                return False
        return True

    def _apply_csm_guard(
        self,
        pose_before_csm: np.ndarray,
        predicted_pose: np.ndarray,
        used_icp: bool,
        used_turn_cal: bool,
    ) -> None:
        """Keep CSM from snapping pose back when LiDAR/odometry already moved."""
        if self._confidence < _MIN_CONF:
            return

        csm_delta = self.pose[:2] - pose_before_csm[:2]
        trans_mag = float(np.linalg.norm(csm_delta))
        turn_mag = abs(float(self._normalize_angle(
            self.pose[2] - pose_before_csm[2]
        )))

        if trans_mag > _CSM_MAX_CORRECTION_M and self._confidence < _CSM_HIGH_CONF:
            csm_conf = self._confidence
            self.pose = pose_before_csm.copy()
            self._confidence = 0.0
            log.debug(
                "CSM rejected: large snap (%.2fm) with low conf %.3f",
                trans_mag, csm_conf,
            )
            return

        if self._last_pose is not None:
            pred_delta = predicted_pose[:2] - self._last_pose[:2]
        else:
            pred_delta = np.zeros(2, dtype=np.float64)
        pred_mag = float(np.linalg.norm(pred_delta))

        if pred_mag > 0.015 and trans_mag > 0.02:
            dot = float(np.dot(csm_delta, pred_delta))
            if dot < -0.15 * max(pred_mag, 1e-6) * max(trans_mag, 1e-6):
                self.pose = pose_before_csm.copy()
                self._confidence = 0.0
                log.debug("CSM rejected: correction opposes motion hint")
                return

        if (used_icp or used_turn_cal) and self._confidence < _CSM_HIGH_CONF:
            if trans_mag > _CSM_REFINE_MAX_M or turn_mag > 0.30:
                self.pose = pose_before_csm.copy()
                self._confidence = 0.0
                log.debug(
                    "CSM rejected: exceeds refine window (d=%.2fm, dθ=%.1f°)",
                    trans_mag, math.degrees(turn_mag),
                )

    def _bearing_profile(self, pts: np.ndarray, n_bins: int) -> np.ndarray:
        """Min range per bearing bin over [-pi, pi)."""
        prof = np.full(n_bins, np.nan, dtype=np.float64)
        if len(pts) < 5:
            return prof

        x, y = pts[:, 0], pts[:, 1]
        rng = np.hypot(x, y)
        valid = (rng >= _MIN_RANGE_M) & (rng <= _MAX_RANGE_M)
        if int(valid.sum()) < 5:
            return prof

        ang = np.arctan2(y[valid], x[valid])
        rng = rng[valid]
        bins = ((ang + math.pi) * (n_bins / (2.0 * math.pi))).astype(int) % n_bins
        for b, rv in zip(bins, rng):
            if np.isnan(prof[b]) or rv < prof[b]:
                prof[b] = rv
        return prof

    def _estimate_turn_rotation(
        self,
        prev_pts: np.ndarray,
        curr_pts: np.ndarray,
        hint_dtheta: float = 0.0,
    ) -> tuple[Optional[float], float]:
        """
        Estimate in-place rotation between consecutive scans by correlating
        bearing-range profiles.  More reliable than ICP for large turn steps.
        """
        n_bins = max(72, _TURN_BEARING_BINS)
        prev_prof = self._bearing_profile(prev_pts, n_bins)
        curr_prof = self._bearing_profile(curr_pts, n_bins)

        valid_prev = np.isfinite(prev_prof)
        if int(valid_prev.sum()) < _TURN_MIN_BINS:
            return None, 0.0

        max_shift = max(1, int(_TURN_MAX_SEARCH_RAD / (2.0 * math.pi) * n_bins))
        if abs(hint_dtheta) > 0.01:
            hint_shift = int(round(hint_dtheta / (2.0 * math.pi) * n_bins))
            shift_lo = hint_shift - max(3, max_shift // 2)
            shift_hi = hint_shift + max(3, max_shift // 2)
        else:
            shift_lo, shift_hi = -max_shift, max_shift

        best_shift = 0
        best_score = 0.0
        tol = _TURN_RANGE_TOL_M

        for shift in range(shift_lo, shift_hi + 1):
            rolled = np.roll(curr_prof, shift)
            both = valid_prev & np.isfinite(rolled)
            n = int(both.sum())
            if n < _TURN_MIN_BINS:
                continue

            diff = np.abs(prev_prof[both] - rolled[both])
            in_tol = diff < tol
            if not np.any(in_tol):
                continue

            score = float(in_tol.sum()) / n
            mean_err = float(diff[in_tol].mean())
            score *= 1.0 - 0.5 * min(1.0, mean_err / max(tol, 1e-6))

            if score > best_score:
                best_score = score
                best_shift = shift

        if best_score < _TURN_MIN_CONF:
            return None, best_score

        # Sub-bin parabolic refinement for finer angle estimates
        dtheta_step = 2.0 * math.pi / n_bins
        refined_shift = float(best_shift)
        if shift_lo < best_shift < shift_hi:
            def _shift_score(s: int) -> float:
                rolled = np.roll(curr_prof, s)
                both = valid_prev & np.isfinite(rolled)
                n = int(both.sum())
                if n < _TURN_MIN_BINS:
                    return 0.0
                diff = np.abs(prev_prof[both] - rolled[both])
                in_tol = diff < tol
                if not np.any(in_tol):
                    return 0.0
                sc = float(in_tol.sum()) / n
                sc *= 1.0 - 0.5 * min(1.0, float(diff[in_tol].mean()) / max(tol, 1e-6))
                return sc

            s_m, s_0, s_p = _shift_score(best_shift - 1), best_score, _shift_score(best_shift + 1)
            denom = s_m - 2.0 * s_0 + s_p
            if abs(denom) > 1e-6:
                refined_shift = best_shift + 0.5 * (s_m - s_p) / denom

        dtheta = refined_shift * dtheta_step
        return self._normalize_angle(dtheta), min(1.0, best_score)

    def _update_angular_calibration(
        self,
        cmd_dtheta: float,
        meas_dtheta: float,
        conf: float,
    ) -> None:
        """Adapt effective angular rate from LiDAR-measured vs command rotation."""
        if abs(cmd_dtheta) < 0.02 or abs(meas_dtheta) < 0.02:
            return
        if cmd_dtheta * meas_dtheta <= 0:
            return

        rate_ratio = abs(meas_dtheta) / abs(cmd_dtheta)
        if rate_ratio < 0.35 or rate_ratio > 2.8:
            return

        weight = _TURN_CALIB_ALPHA * min(1.0, conf)
        target = self._ang_rps_eff * rate_ratio
        self._ang_rps_eff = (1.0 - weight) * self._ang_rps_eff + weight * target
        self._ang_rps_eff = max(0.05, min(4.0, self._ang_rps_eff))
        self._turn_cal_samples += 1

    def _estimate_scan_motion(
        self,
        local_pts: np.ndarray,
        max_rot_rad: float | None = None,
    ) -> tuple[Optional[np.ndarray], float]:
        """
        Estimate robot motion by aligning the current LiDAR scan to the previous
        scan. The transform is current-local -> previous-local; composing it
        with the previous world pose gives a LiDAR-only pose prediction.
        """
        if not _ICP_ENABLED or self._last_pts is None or self._last_pose is None:
            return None, 0.0
        if len(local_pts) < _ICP_MIN_PAIRS or len(self._last_pts) < _ICP_MIN_PAIRS:
            return None, 0.0

        source = self._sample_points(local_pts, _ICP_MAX_PTS)
        target = self._sample_points(self._last_pts, _ICP_MAX_PTS)
        if len(source) < _ICP_MIN_PAIRS or len(target) < _ICP_MIN_PAIRS:
            return None, 0.0

        tree = cKDTree(target)
        transformed = source.copy()
        total_R = np.eye(2, dtype=np.float64)
        total_t = np.zeros(2, dtype=np.float64)
        last_rmse = _ICP_MAX_PAIR_M
        pair_count = 0

        for _ in range(max(1, _ICP_ITERS)):
            dist, idx = tree.query(transformed)
            valid = dist <= _ICP_MAX_PAIR_M
            if int(valid.sum()) < _ICP_MIN_PAIRS:
                break

            valid_dist = dist[valid]
            keep_limit = np.percentile(valid_dist, 70)
            keep = valid.copy()
            keep[valid] = valid_dist <= keep_limit
            if int(keep.sum()) < _ICP_MIN_PAIRS:
                keep = valid

            src = transformed[keep]
            dst = target[idx[keep]]
            R_inc, t_inc = self._rigid_fit(src, dst)

            transformed = (R_inc @ transformed.T).T + t_inc
            total_R = R_inc @ total_R
            total_t = R_inc @ total_t + t_inc
            pair_count = int(keep.sum())
            last_rmse = float(np.sqrt(np.mean(np.square(dist[keep]))))

        if pair_count < _ICP_MIN_PAIRS:
            return None, 0.0

        dtheta = math.atan2(float(total_R[1, 0]), float(total_R[0, 0]))
        step_m = float(np.linalg.norm(total_t))
        rot_limit = max_rot_rad if max_rot_rad is not None else _ICP_MAX_ROT_RAD
        if step_m > _ICP_MAX_STEP_M or abs(dtheta) > rot_limit:
            return None, 0.0

        pair_score = min(1.0, pair_count / max(float(len(source)), 1.0))
        error_score = max(0.0, 1.0 - (last_rmse / max(_ICP_MAX_PAIR_M, 1e-6)))
        confidence = max(0.0, min(1.0, pair_score * error_score))

        c, s = math.cos(float(self._last_pose[2])), math.sin(float(self._last_pose[2]))
        prev_R = np.array([[c, -s], [s, c]], dtype=np.float64)
        pose = np.array([
            float(self._last_pose[0]) + float((prev_R @ total_t)[0]),
            float(self._last_pose[1]) + float((prev_R @ total_t)[1]),
            float(self._last_pose[2]) + dtheta,
        ], dtype=np.float64)
        return pose, confidence

    @staticmethod
    def _sample_points(pts: np.ndarray, max_pts: int) -> np.ndarray:
        if len(pts) <= max_pts:
            return np.asarray(pts, dtype=np.float64)
        idx = np.round(np.linspace(0, len(pts) - 1, max_pts)).astype(int)
        return np.asarray(pts[idx], dtype=np.float64)

    @staticmethod
    def _rigid_fit(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        src_mean = src.mean(axis=0)
        dst_mean = dst.mean(axis=0)
        src_c = src - src_mean
        dst_c = dst - dst_mean
        H = src_c.T @ dst_c
        U, _S, Vt = np.linalg.svd(H)
        R = Vt.T @ U.T
        if np.linalg.det(R) < 0:
            Vt[-1, :] *= -1
            R = Vt.T @ U.T
        t = dst_mean - R @ src_mean
        return R, t

    def _ensure_bounds(self, world_pts: np.ndarray) -> None:
        """Auto-resize the map so robot + scan endpoints always fit."""
        margin = max(0.0, _RESIZE_MARGIN_M)
        rx, ry = float(self.pose[0]), float(self.pose[1])

        if world_pts.size:
            xmin = min(rx, float(np.min(world_pts[:, 0]))) - margin
            xmax = max(rx, float(np.max(world_pts[:, 0]))) + margin
            ymin = min(ry, float(np.min(world_pts[:, 1]))) - margin
            ymax = max(ry, float(np.max(world_pts[:, 1]))) + margin
        else:
            xmin = rx - margin; xmax = rx + margin
            ymin = ry - margin; ymax = ry + margin

        cur_xmin = self.origin_x
        cur_ymin = self.origin_y
        cur_xmax = self.origin_x + self.n * self.resolution
        cur_ymax = self.origin_y + self.n * self.resolution

        need_left = xmin < cur_xmin
        need_right = xmax > cur_xmax
        need_bottom = ymin < cur_ymin
        need_top = ymax > cur_ymax
        if not (need_left or need_right or need_bottom or need_top):
            return

        def _cells(delta_m: float) -> int:
            if delta_m <= 0:
                return 0
            cells = int(math.ceil(delta_m / self.resolution))
            step = self._resize_step_cells
            return int(math.ceil(cells / step) * step)

        pad_left = _cells(cur_xmin - xmin) if need_left else 0
        pad_right = _cells(xmax - cur_xmax) if need_right else 0
        pad_bottom = _cells(cur_ymin - ymin) if need_bottom else 0
        pad_top = _cells(ymax - cur_ymax) if need_top else 0
        if pad_left == pad_right == pad_bottom == pad_top == 0:
            return

        new_n = self.n + pad_left + pad_right  # square map
        # If padding differs between x and y, make square by taking max pads
        new_n = max(new_n, self.n + pad_bottom + pad_top)

        # Recompute pads to match new_n while preserving requested side growth
        extra_x = new_n - (self.n + pad_left + pad_right)
        extra_y = new_n - (self.n + pad_bottom + pad_top)
        pad_left += extra_x // 2
        pad_right += extra_x - (extra_x // 2)
        pad_bottom += extra_y // 2
        pad_top += extra_y - (extra_y // 2)

        log.info(
            "Auto-resize SLAM map -> %dx%d (add L%d R%d B%d T%d cells)",
            new_n, new_n, pad_left, pad_right, pad_bottom, pad_top,
        )

        new_log = np.zeros((new_n, new_n), dtype=np.float32)
        new_blur = np.zeros((new_n, new_n), dtype=np.float32)
        new_log[pad_bottom:pad_bottom + self.n, pad_left:pad_left + self.n] = self._log
        new_blur[pad_bottom:pad_bottom + self.n, pad_left:pad_left + self.n] = self._prob_blur

        self._log = new_log
        self._prob_blur = new_blur

        self.origin_x -= pad_left * self.resolution
        self.origin_y -= pad_bottom * self.resolution
        self.n = new_n

    # ── Correlative Scan Matcher (Cartographer CSM) ───────────────────────────

    def _scan_match(
        self,
        local_pts: np.ndarray,
        search_angle: float | None = None,
    ) -> np.ndarray:
        """
        Cartographer-style CSM:
          For each candidate rotation θ_i in [current_θ ± SEARCH_ANGLE]:
            1. Rasterise scan at pose (x, y, θ_i) → binary image S_i
            2. FFT cross-correlate S_i with blurred probability map P
            3. Peak location gives the best (dx, dy) for that rotation
          Choose (dx*, dy*, θ_i*) that maximises the correlation peak.
        """
        ang_window = search_angle if search_angle is not None else _SEARCH_ANGLE
        n_ang   = max(5, int(2 * ang_window / 0.05) + 1)
        thetas  = np.linspace(self.pose[2] - ang_window,
                              self.pose[2] + ang_window, n_ang)
        s_cells = max(1, int(_SEARCH_RADIUS_M / self.resolution))

        bmap     = self._prob_blur
        fft_bmap = np.fft.fft2(bmap)

        best_score = -np.inf
        best_pose  = self.pose.copy()
        ch, cw     = self.n // 2, self.n // 2

        for theta in thetas:
            cand_pose = np.array([self.pose[0], self.pose[1], theta])
            scan_img  = self._rasterise(local_pts, cand_pose)

            if scan_img.sum() < 1:
                continue

            # FFT cross-correlation:  corr[dy, dx] = score of shift (dy, dx)
            corr = np.real(np.fft.ifft2(fft_bmap * np.conj(np.fft.fft2(scan_img))))
            corr = np.fft.fftshift(corr)

            # Restrict search to ±s_cells
            r0 = max(0, ch - s_cells);  r1 = min(self.n, ch + s_cells + 1)
            c0 = max(0, cw - s_cells);  c1 = min(self.n, cw + s_cells + 1)
            region = corr[r0:r1, c0:c1]

            if region.size == 0:
                continue

            best_idx = np.unravel_index(np.argmax(region), region.shape)
            score    = float(region[best_idx])

            if score > best_score:
                best_score = score
                dy = (best_idx[0] + r0 - ch) * self.resolution
                dx = (best_idx[1] + c0 - cw) * self.resolution
                best_pose = np.array([
                    self.pose[0] + dx,
                    self.pose[1] + dy,
                    theta,
                ])

        # Normalise to 0-1 confidence
        denom = float(bmap.sum()) * float(len(local_pts)) / self.n + 1e-9
        self._confidence = max(0.0, min(1.0, best_score / denom))

        if self._confidence >= _MIN_CONF:
            self.pose = best_pose
            log.debug("CSM  conf=%.3f  pose=(%.2f, %.2f, %.1f°)",
                      self._confidence, *self.pose[:2],
                      math.degrees(self.pose[2]))
        else:
            log.debug("CSM low confidence %.3f – pose unchanged.", self._confidence)

        return self._transform(local_pts, self.pose)

    # ── Rasterisation ─────────────────────────────────────────────────────────

    def _rasterise(self, local_pts: np.ndarray,
                   pose: np.ndarray) -> np.ndarray:
        """Project local scan points to world frame and paint onto a grid."""
        img   = np.zeros((self.n, self.n), dtype=np.float32)
        world = self._transform(local_pts, pose)
        cols  = ((world[:, 0] - self.origin_x) / self.resolution).astype(int)
        rows  = ((world[:, 1] - self.origin_y) / self.resolution).astype(int)
        valid = (rows >= 0) & (rows < self.n) & (cols >= 0) & (cols < self.n)
        img[rows[valid], cols[valid]] = 1.0
        return img

    # ── Log-odds ray casting ──────────────────────────────────────────────────

    def _ray_cast_update(self, world_pts: np.ndarray) -> None:
        """
        For each scan endpoint: mark free cells along the ray (Bresenham),
        mark the endpoint cell as occupied (log-odds update).
        """
        rx, ry = self.pose[0], self.pose[1]
        robot  = _world_to_cell(rx, ry, self.origin_x, self.origin_y,
                                self.resolution, self.n, self.n)

        for x, y in world_pts:
            end = _world_to_cell(x, y, self.origin_x, self.origin_y,
                                 self.resolution, self.n, self.n)
            if end is None:
                continue

            # Occupied endpoint
            self._log[end] = min(_LOG_MAX, self._log[end] + _LOG_OCC)

            if robot is None:
                continue

            # Free-space ray using numpy linspace (vectorised Bresenham-equivalent)
            er, ec = end
            rr0, rc0 = robot
            n_steps  = max(abs(er - rr0), abs(ec - rc0)) + 1
            if n_steps < 2:
                continue
            rs = np.round(np.linspace(rr0, er, n_steps)).astype(int)[:-1]
            cs = np.round(np.linspace(rc0, ec, n_steps)).astype(int)[:-1]

            # Clamp to map bounds
            valid = (rs >= 0) & (rs < self.n) & (cs >= 0) & (cs < self.n)
            rs, cs = rs[valid], cs[valid]
            if len(rs):
                # Don't let repeated passes \"erase\" established obstacles:
                # once a cell is confidently occupied (> _OCC_LOCK), ignore free updates.
                cur = self._log[rs, cs]
                mask = cur < _OCC_LOCK
                if np.any(mask):
                    cur2 = cur.copy()
                    cur2[mask] = np.clip(cur2[mask] - _LOG_FREE, -_LOG_MAX, _LOG_MAX)
                    self._log[rs, cs] = cur2

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _refresh_blur(self) -> None:
        """Recompute the Gaussian-blurred probability map used by CSM."""
        prob = 1.0 / (1.0 + np.exp(-self._log))
        self._prob_blur = gaussian_filter(prob, sigma=self._blur_sigma).astype(
            np.float32)

    @staticmethod
    def _transform(pts: np.ndarray, pose: np.ndarray) -> np.ndarray:
        """Rigid-body transform: local frame → world frame."""
        c, s = math.cos(pose[2]), math.sin(pose[2])
        R    = np.array([[c, -s], [s, c]])
        return (R @ pts.T).T + pose[:2]

    def reset(self) -> None:
        """Clear all SLAM state and restart from origin."""
        with self._lock:
            self._log[:]       = 0.0
            self._prob_blur[:] = 0.0
            self.pose[:]       = 0.0
            self._last_pts     = None
            self._last_pose    = None
            self._last_world_pts = None
            self.trajectory.clear()
            self._scans        = 0
            self._confidence   = 1.0
            self._icp_confidence = 0.0
            self._turn_cal_confidence = 0.0
            self._turn_cal_samples = 0
            self._last_turn_dtheta = None
            self._ang_rps_eff = _ANG_RPS
            self._rejected_scans = 0
            self._dirty        = True
            self._active_cmd   = None
            self._last_motion_t = time.time()
            self._last_scan_t = None
        log.info("SLAM state reset.")

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self, status: str = "running", uptime_s: float = 0.0,
                crop: bool = False) -> dict:
        """Serialise current SLAM state to the JSON output schema."""
        with self._lock:
            self._integrate_motion(time.time())
            cells           = np.full(self.n * self.n, -1, dtype=np.int8)
            flat_log        = self._log.flatten()
            cells[flat_log >  0.1] = 100
            cells[flat_log < -0.1] = 0
            coverage = round(
                100.0 * float((self._log != 0).sum()) / (self.n * self.n), 2)
            map_width = self.n
            map_height = self.n
            map_origin_x = self.origin_x
            map_origin_y = self.origin_y
            map_cells = cells

            if crop:
                map_cells, map_width, map_height, map_origin_x, map_origin_y = (
                    self._crop_cells_for_view(cells)
                )

            return {
                "version":     1,
                "timestamp":   _now_iso(),
                "slam_status": status,
                "robot_pose":  {
                    "x":          round(float(self.pose[0]), 4),
                    "y":          round(float(self.pose[1]), 4),
                    "theta":      round(float(self.pose[2]), 4),
                    "confidence": round(self._confidence, 4),
                    "icp_confidence": round(self._icp_confidence, 4),
                    "turn_cal_confidence": round(self._turn_cal_confidence, 4),
                },
                "map": {
                    "width":      map_width,
                    "height":     map_height,
                    "resolution": self.resolution,
                    "origin":     {
                        "x": round(map_origin_x, 4),
                        "y": round(map_origin_y, 4),
                    },
                    "cells": map_cells.tolist(),
                },
                "trajectory": list(self.trajectory),
                "latest_scan": (
                    None
                    if self._last_world_pts is None
                    else [{
                        "x": round(float(p[0]), 4),
                        "y": round(float(p[1]), 4),
                    } for p in self._last_world_pts[:_MAX_SCAN_PTS]]
                ),
                "stats": {
                    "scans_processed":  self._scans,
                    "rejected_scans":   self._rejected_scans,
                    "map_coverage_pct": coverage,
                    "uptime_s":         round(uptime_s, 1),
                    "turn_calibration": {
                        "enabled":           _TURN_CALIB_ENABLED,
                        "angular_rps_nominal": round(_ANG_RPS, 4),
                        "angular_rps_effective": round(self._ang_rps_eff, 4),
                        "scale": round(self._ang_rps_eff / max(_ANG_RPS, 1e-6), 4),
                        "samples": self._turn_cal_samples,
                        "last_dtheta_deg": (
                            None
                            if self._last_turn_dtheta is None
                            else round(math.degrees(self._last_turn_dtheta), 2)
                        ),
                    },
                },
            }

    def _crop_cells_for_view(
        self,
        cells: np.ndarray,
    ) -> tuple[np.ndarray, int, int, float, float]:
        grid = cells.reshape((self.n, self.n))
        known = np.argwhere(grid != -1)

        robot = _world_to_cell(
            float(self.pose[0]), float(self.pose[1]),
            self.origin_x, self.origin_y,
            self.resolution, self.n, self.n,
        )
        if robot is not None:
            known = np.vstack([known, np.asarray([robot], dtype=np.int64)])

        traj_cells = []
        for pose in self.trajectory:
            cell = _world_to_cell(
                float(pose.get("x", 0.0)), float(pose.get("y", 0.0)),
                self.origin_x, self.origin_y,
                self.resolution, self.n, self.n,
            )
            if cell is not None:
                traj_cells.append(cell)
        if traj_cells:
            known = np.vstack([known, np.asarray(traj_cells, dtype=np.int64)])

        if known.size == 0:
            center = self.n // 2
            known = np.asarray([[center, center]], dtype=np.int64)

        pad = max(1, int(round(_VIEW_PADDING_M / self.resolution)))
        min_size = max(1, int(round(_VIEW_MIN_SIZE_M / self.resolution)))

        r0 = int(np.min(known[:, 0])) - pad
        r1 = int(np.max(known[:, 0])) + pad + 1
        c0 = int(np.min(known[:, 1])) - pad
        c1 = int(np.max(known[:, 1])) + pad + 1

        height = r1 - r0
        width = c1 - c0
        if height < min_size:
            extra = min_size - height
            r0 -= extra // 2
            r1 += extra - (extra // 2)
        if width < min_size:
            extra = min_size - width
            c0 -= extra // 2
            c1 += extra - (extra // 2)

        r0 = max(0, r0)
        c0 = max(0, c0)
        r1 = min(self.n, r1)
        c1 = min(self.n, c1)

        view = grid[r0:r1, c0:c1]
        origin_x = self.origin_x + c0 * self.resolution
        origin_y = self.origin_y + r0 * self.resolution
        return view.reshape(-1), int(c1 - c0), int(r1 - r0), origin_x, origin_y


# ═════════════════════════════════════════════════════════════════════════════
# SLAM Service  (MQTT + background writer; integrates with Flask app)
# ═════════════════════════════════════════════════════════════════════════════

class SlamService:
    """
    Wraps CartographerSLAM with:
    • A dedicated paho-mqtt client that mirrors the main broker connection.
    • A background thread that writes slam_map.json every WRITE_INTERVAL seconds.
    • A connection-monitor thread that auto-connects when the Flask
      MQTTService connects (no separate user action required).
    """

    def __init__(self) -> None:
        self.slam        = CartographerSLAM()
        self._client     = mqtt.Client(client_id="slam_service")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

        self._broker_ip: str | None = None
        self.connected   = False
        self._stop       = threading.Event()
        self._status     = "waiting"
        self._t_start    = time.time()

        self._writer_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._reset_flag = False   # set True by /api/slam/reset route

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start_background(self) -> None:
        """Start background threads (called from Flask create_app)."""
        self._writer_thread  = threading.Thread(
            target=self._writer_loop,  daemon=True, name="slam-writer")
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="slam-monitor")
        self._writer_thread.start()
        self._monitor_thread.start()
        log.info("SLAM service background threads started.")

    def connect(self, broker_ip: str) -> None:
        """Connect (or reconnect) the SLAM MQTT client to a broker."""
        if self._broker_ip == broker_ip and self.connected:
            return
        try:
            if self.connected:
                self._client.disconnect()
            self._client.connect(broker_ip, _BROKER_PORT, _MQTT_TIMEOUT)
            self._client.loop_start()
            self._broker_ip = broker_ip
            log.info("SLAM client connecting to %s:%d", broker_ip, _BROKER_PORT)
        except Exception as exc:
            log.error("SLAM broker connection failed: %s", exc)
            self._status = "error"

    def stop(self) -> None:
        self._stop.set()
        try:
            self._client.disconnect()
        except Exception:
            pass

    def request_reset(self) -> None:
        """Signal the next write cycle to reset all SLAM state."""
        self._reset_flag = True

    # ── MQTT callbacks ────────────────────────────────────────────────────────

    def _on_connect(self, client, _ud, _flags, rc) -> None:
        if rc == 0:
            client.subscribe(_SCAN_TOPIC)
            client.subscribe(_GRID_TOPIC)
            client.subscribe(_CMD_TOPIC)
            self.connected = True
            self._status   = "running"
            log.info("SLAM MQTT connected (rc=%d). Subscribed to %s, %s, %s.",
                     rc, _SCAN_TOPIC, _GRID_TOPIC, _CMD_TOPIC)
        else:
            log.warning("SLAM MQTT connect failed rc=%d", rc)
            self._status = "error"

    def _on_message(self, _client, _ud, message) -> None:
        raw = message.payload.decode(errors="replace").strip()
        if not raw:
            return

        # Commands are often plain strings. Only require JSON for scan/grid topics.
        if message.topic == _CMD_TOPIC:
            self.slam.apply_command(raw)
            return

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            log.debug("Non-JSON on %s – skipped.", message.topic)
            return

        if message.topic == _SCAN_TOPIC:
            self.slam.process_raw_scan(payload)
        elif message.topic == _GRID_TOPIC:
            self.slam.process_grid_scan(payload)
        return

    # ── Background threads ────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        """
        Watch the main MQTTService (from mqtt_service.py) and mirror
        its broker connection so the user only needs to click Connect once.
        """
        while not self._stop.wait(timeout=1.5):
            try:
                from app.services.mqtt_service import mqtt_service as ms
                if ms.connected and ms.broker_ip:
                    self.connect(ms.broker_ip)
            except Exception:
                pass

    def _writer_loop(self) -> None:
        """Atomically write slam_map.json every WRITE_INTERVAL seconds."""
        while not self._stop.wait(timeout=_WRITE_INTERVAL):
            if self._reset_flag:
                self.slam.reset()
                self._reset_flag = False

            if not self.slam._dirty and _OUTPUT_PATH.exists():
                continue

            uptime = time.time() - self._t_start
            data   = self.slam.to_dict(status=self._status, uptime_s=uptime)
            self._write_json(data)
            self.slam._dirty = False

    def _write_json(self, data: dict) -> None:
        """Write to a temp file then rename (atomic on POSIX; best-effort on Windows)."""
        try:
            _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = _OUTPUT_PATH.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, separators=(",", ":")),
                                encoding="utf-8")
            # On Windows os.replace is atomic within the same filesystem
            os.replace(tmp_path, _OUTPUT_PATH)
            log.debug("slam_map.json written (%d scans).",
                      data["stats"]["scans_processed"])
        except Exception as exc:
            log.error("Failed to write slam_map.json: %s", exc)

    # ── Status / dict helpers (used by Flask routes) ──────────────────────────

    def get_map(self, crop: bool = False) -> dict:
        uptime = time.time() - self._t_start
        return self.slam.to_dict(status=self._status, uptime_s=uptime, crop=crop)

    def get_status(self) -> dict:
        with self.slam._lock:
            self.slam._integrate_motion(time.time())
            return {
                "slam_status":      self._status,
                "connected":        self.connected,
                "broker_ip":        self._broker_ip,
                "scans_processed":  self.slam._scans,
                "rejected_scans":   self.slam._rejected_scans,
                "confidence":       round(self.slam._confidence, 4),
                "icp_confidence":   round(self.slam._icp_confidence, 4),
                "turn_cal_confidence": round(self.slam._turn_cal_confidence, 4),
                "angular_rps_effective": round(self.slam._ang_rps_eff, 4),
                "robot_pose": {
                    "x":     round(float(self.slam.pose[0]), 4),
                    "y":     round(float(self.slam.pose[1]), 4),
                    "theta": round(float(self.slam.pose[2]), 4),
                },
                "map_coverage_pct": round(
                    100.0 * float((self.slam._log != 0).sum())
                    / (self.slam.n ** 2), 2),
            }


# ── Global singleton (used by Flask routes) ───────────────────────────────────
slam_service = SlamService()


# ═════════════════════════════════════════════════════════════════════════════
# Standalone entry-point
# ═════════════════════════════════════════════════════════════════════════════

def _main() -> None:
    """Run the SLAM service as a standalone process."""
    import argparse

    global _OUTPUT_PATH  # may be overridden by --output CLI argument

    parser = argparse.ArgumentParser(
        description="Yahboom SLAM service (Cartographer-inspired)")
    parser.add_argument("--broker",  default=os.getenv("MQTT_BROKER_IP", ""),
                        help="MQTT broker IP / hostname")
    parser.add_argument("--output",  default=str(_OUTPUT_PATH),
                        help="Path to slam_map.json output file")
    parser.add_argument("--reset",   action="store_true",
                        help="Delete existing map on start")
    args = parser.parse_args()

    # Override output path if supplied on CLI
    _OUTPUT_PATH = Path(args.output)

    if args.reset and _OUTPUT_PATH.exists():
        _OUTPUT_PATH.unlink()
        log.info("Existing slam_map.json deleted.")

    svc = SlamService()
    svc._t_start = time.time()

    # Write an initial "waiting" file immediately
    svc._write_json(svc.slam.to_dict(status="waiting", uptime_s=0.0))

    if args.broker:
        svc.connect(args.broker)
    else:
        log.info("No broker specified – waiting for connection via monitor thread.")

    # Start background threads
    svc.start_background()

    log.info("SLAM service running. Output: %s", _OUTPUT_PATH)
    log.info("Press Ctrl-C to stop.")
    try:
        while True:
            time.sleep(5)
            st = svc.get_status()
            log.info("Status: %s  scans=%d  pose=(%.2f, %.2f, %.1f°)  coverage=%.1f%%",
                     st["slam_status"], st["scans_processed"],
                     st["robot_pose"]["x"], st["robot_pose"]["y"],
                     math.degrees(st["robot_pose"]["theta"]),
                     st["map_coverage_pct"])
    except KeyboardInterrupt:
        log.info("Shutting down SLAM service.")
        svc.stop()


if __name__ == "__main__":
    _main()
