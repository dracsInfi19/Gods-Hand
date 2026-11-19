"""Heartbeat watchdog: ACTIVE -> STALE (3 missed) -> LOST (10 missed).

One tiny FSM per node; in hardware this becomes an array of counters running in parallel.
"""
from typing import Dict, Optional, Tuple

ACTIVE, STALE, LOST = "ACTIVE", "STALE", "LOST"


class Watchdog:
    def __init__(self, period: float = 45.0, stale_after: int = 3, lost_after: int = 10):
        self.period, self.stale_after, self.lost_after = period, stale_after, lost_after
        self.last_seen: Dict[int, float] = {}
        self.last_pos: Dict[int, Optional[Tuple[float, float]]] = {}

    def heard(self, node_id: int, ts: float, pos=None) -> None:
        self.last_seen[node_id] = ts
        if pos is not None:
            self.last_pos[node_id] = pos

    def missed(self, node_id: int, now: float) -> int:
        return int(max(0.0, now - self.last_seen[node_id]) // self.period)

    def state(self, node_id: int, now: float) -> str:
        m = self.missed(node_id, now)
        return LOST if m >= self.lost_after else STALE if m >= self.stale_after else ACTIVE

    def states(self, now: float) -> Dict[int, str]:
        return {n: self.state(n, now) for n in self.last_seen}
