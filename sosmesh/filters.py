"""False-alarm filtering: two-step button, retry/duplicate/spam control,
sanity checks, confidence score and the verification ladder."""
import math
from collections import deque
from typing import Dict, Optional, Set

from .gps import MAX_HDOP, MIN_SATS, to_xy


# ---- node side ------------------------------------------------------------
class TwoStepButton:
    """Send only on a 3 s hold, or on two quick presses within 2 s."""

    def __init__(self, hold_s: float = 3.0, double_s: float = 2.0):
        self.hold_s, self.double_s = hold_s, double_s
        self._down: Optional[float] = None
        self._last_quick: Optional[float] = None

    def press(self, t: float) -> None:
        self._down = t

    def release(self, t: float) -> bool:
        if self._down is None:
            return False
        held, self._down = t - self._down, None
        if held >= self.hold_s:
            self._last_quick = None
            return True
        if self._last_quick is not None and t - self._last_quick <= self.double_s:
            self._last_quick = None
            return True
        self._last_quick = t
        return False


class NodeBuffer:
    """Store-and-forward: keep unACKed SOS packets and retry every N seconds."""

    def __init__(self, retry_s: float = 10.0, max_len: int = 16):
        self.retry_s, self.max_len = retry_s, max_len
        self.pending: Dict[int, list] = {}      # seq -> [packet, next_try_ts]

    def push(self, pkt, now: float) -> None:
        if len(self.pending) >= self.max_len:
            self.pending.pop(min(self.pending))  # drop oldest
        self.pending[pkt.seq] = [pkt, now]

    def due(self, now: float):
        out = []
        for entry in self.pending.values():
            if now >= entry[1]:
                entry[1] = now + self.retry_s
                out.append(entry[0])
        return out

    def ack(self, seq: int) -> None:
        self.pending.pop(seq, None)


# ---- gateway side ---------------------------------------------------------
class SpamFilter:
    """Same seq = retry (ignore). New seq inside the window = merged press.
    More than max_per_window distinct presses per window = rate limited."""

    def __init__(self, window: float = 60.0, max_per_window: int = 6):
        self.window, self.max = window, max_per_window
        self.seen: Dict[int, Set[int]] = {}
        self.times: Dict[int, deque] = {}

    def check(self, node_id: int, seq: int, now: float) -> str:
        seen = self.seen.setdefault(node_id, set())
        if seq in seen:
            return "RETRY"
        seen.add(seq)
        dq = self.times.setdefault(node_id, deque())
        while dq and now - dq[0] > self.window:
            dq.popleft()
        dq.append(now)
        return "RATE_LIMITED" if len(dq) > self.max else "OK"


class SanityChecker:
    """Returns a reason string if the fix is not trustworthy, else None."""

    def __init__(self, origin, zone_center=(0.0, 0.0), zone_radius=5000.0, max_speed=40.0):
        self.origin, self.zc, self.zr, self.max_speed = origin, zone_center, zone_radius, max_speed
        self.last: Dict[int, tuple] = {}

    def check(self, node_id: int, lat: float, lon: float, hdop: float, sats: int, ts: float):
        if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
            return "invalid_coordinates"
        if not (hdop == hdop and hdop <= MAX_HDOP) or sats < MIN_SATS:
            return "poor_gps_quality"
        x, y = to_xy(lat, lon, self.origin)
        if math.dist((x, y), self.zc) > self.zr:
            return "outside_flood_zone"
        prev = self.last.get(node_id)
        if prev is not None:
            dt = max(ts - prev[2], 1.0)
            if math.dist((x, y), prev[:2]) / dt > self.max_speed:
                return "impossible_jump"
        self.last[node_id] = (x, y, ts)
        return None


MOTION_SCORE = {"moving": 1.0, "still": 0.6, "dropped": 0.0}
W_GPS, W_REP, W_MOTION, W_CV = 0.35, 0.20, 0.15, 0.30
DISPATCH_AT, VERIFY_AT = 0.65, 0.40


def confidence(gps_q: float, repeat_consistency: float, motion: str, cv: float) -> float:
    """Confidence = weighted GPS_quality + repeat_consistency + motion_flag + CV."""
    return (W_GPS * gps_q + W_REP * repeat_consistency
            + W_MOTION * MOTION_SCORE.get(motion, 0.5) + W_CV * cv)


def decision(conf: float) -> str:
    """Verification ladder."""
    if conf >= DISPATCH_AT:
        return "DISPATCH"
    return "VERIFY_WITH_DRONE" if conf >= VERIFY_AT else "UNVERIFIED"
