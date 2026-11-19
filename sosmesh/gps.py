"""Location precision: HDOP gating, Kalman smoothing, dead-reckoning fallback."""
import math
from typing import List, Optional, Tuple

R_EARTH = 6371000.0
DEFAULT_ORIGIN = (10.0, 76.3)   # arbitrary demo origin (lat, lon)
MAX_HDOP = 5.0
MIN_SATS = 4
WALK_SPEED = 1.5                # m/s, used to grow the uncertainty circle


def to_xy(lat: float, lon: float, origin=DEFAULT_ORIGIN) -> Tuple[float, float]:
    x = math.radians(lon - origin[1]) * R_EARTH * math.cos(math.radians(origin[0]))
    y = math.radians(lat - origin[0]) * R_EARTH
    return x, y


def to_latlon(x: float, y: float, origin=DEFAULT_ORIGIN) -> Tuple[float, float]:
    lat = origin[0] + math.degrees(y / R_EARTH)
    lon = origin[1] + math.degrees(x / (R_EARTH * math.cos(math.radians(origin[0]))))
    return lat, lon


def fix_ok(hdop: float, sats: int) -> bool:
    return hdop == hdop and hdop <= MAX_HDOP and sats >= MIN_SATS


def gps_quality(hdop: float, sats: int) -> float:
    """0..1 quality from HDOP (70%) and satellite count (30%)."""
    if not fix_ok(hdop, sats):
        return 0.0
    q_h = max(0.0, min(1.0, 1.0 - (hdop - 1.0) / 4.0))
    q_s = max(0.0, min(1.0, (sats - 4) / 8.0))
    return 0.7 * q_h + 0.3 * q_s


class Kalman1D:
    """Constant-position model; measurement noise comes from HDOP."""

    def __init__(self, q: float = 0.5):
        self.q, self.x, self.p = q, None, 0.0

    def update(self, z: float, r: float, dt: float) -> float:
        if self.x is None:
            self.x, self.p = z, r
            return self.x
        self.p += self.q * max(dt, 0.0)
        k = self.p / (self.p + r)
        self.x += k * (z - self.x)
        self.p *= (1.0 - k)
        return self.x


class PositionFilter:
    def __init__(self):
        self.kx, self.ky = Kalman1D(), Kalman1D()
        self.last_ts: Optional[float] = None

    def update(self, x: float, y: float, hdop: float, ts: float):
        r = (4.0 * hdop) ** 2
        dt = 0.0 if self.last_ts is None else ts - self.last_ts
        self.last_ts = ts
        self.kx.update(x, r, dt)
        self.ky.update(y, r, dt)
        return self.position()

    def position(self) -> Optional[Tuple[float, float]]:
        return None if self.kx.x is None else (self.kx.x, self.ky.x)

    def sigma(self) -> float:
        return math.sqrt(self.kx.p) if self.kx.x is not None else float("inf")

    def uncertainty(self, now: float) -> float:
        """Radius (m) of the uncertainty circle; grows while GPS is lost."""
        if self.last_ts is None:
            return float("inf")
        return self.sigma() + WALK_SPEED * max(0.0, now - self.last_ts)


def centroid_from_neighbors(neigh: List[Tuple[float, float, float]]) -> Optional[Tuple[float, float]]:
    """Fallback position: LQ-weighted centroid of neighbours' (x, y, lq)."""
    w = sum(q for _, _, q in neigh)
    if w <= 0:
        return None
    return (sum(x * q for x, _, q in neigh) / w, sum(y * q for _, y, q in neigh) / w)
