"""Live mesh graph, Dijkstra with cost 1/LQ, alternate hop, gap healing."""
import heapq
import math
from collections import deque
from typing import Dict, Optional, Set, Tuple

from .link_quality import MIN_USABLE_LQ, edge_cost, link_quality
from .models import Heartbeat

Adj = Dict[int, Dict[int, float]]


class MeshGraph:
    def __init__(self):
        self.reports: Dict[int, Dict[int, float]] = {}   # node -> {neighbour: LQ}

    def update(self, hb: Heartbeat) -> None:
        self.reports[hb.node_id] = {
            v: link_quality(s.snr, s.rssi, s.delivery) for v, s in hb.neighbors.items()}

    def adjacency(self, alive: Set[int]) -> Adj:
        """Undirected graph over alive nodes only; LQ averaged if both ends report."""
        lqs: Dict[frozenset, list] = {}
        for u in alive:
            for v, q in self.reports.get(u, {}).items():
                if v in alive and v != u and q >= MIN_USABLE_LQ:
                    lqs.setdefault(frozenset((u, v)), []).append(q)
        adj: Adj = {u: {} for u in alive}
        for key, qs in lqs.items():
            u, v = tuple(key)
            c = edge_cost(sum(qs) / len(qs))
            adj[u][v] = c
            adj[v][u] = c
        return adj


def dijkstra(adj: Adj, src: int, dst: int) -> Optional[Tuple[list, float]]:
    if src not in adj or dst not in adj:
        return None
    dist, prev, pq = {src: 0.0}, {}, [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == dst:
            path = [dst]
            while path[-1] != src:
                path.append(prev[path[-1]])
            return path[::-1], d
        if d > dist.get(u, math.inf):
            continue
        for v, c in adj[u].items():
            nd = d + c
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    return None


def reachable(adj: Adj, src: int) -> Set[int]:
    seen, q = {src}, deque([src])
    while q:
        u = q.popleft()
        for v in adj.get(u, {}):
            if v not in seen:
                seen.add(v)
                q.append(v)
    return seen


def best_alternate(adj: Adj, src: int, dst: int, failed_hop: int) -> Optional[Tuple[list, float]]:
    """Path from src to dst that avoids failed_hop (re-send through best other neighbour)."""
    pruned = {u: {v: c for v, c in nb.items() if v != failed_hop}
              for u, nb in adj.items() if u != failed_hop}
    return dijkstra(pruned, src, dst)


def best_relay_point(connected: Dict[int, Tuple[float, float]],
                     missing: Dict[int, Tuple[float, float]],
                     radio_range: float, step: float = 25.0):
    """Gap healing: best drone-relay spot = reachable from the connected network
    and covering the most missing nodes. Returns (x, y, covered_ids) or None."""
    if not missing or not connected:
        return None
    pts = list(connected.values()) + list(missing.values())
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    best, best_key = None, None
    x = x0
    while x <= x1:
        y = y0
        while y <= y1:
            if any(math.dist((x, y), c) <= radio_range for c in connected.values()):
                cov = [n for n, p in missing.items() if math.dist((x, y), p) <= radio_range]
                if cov:
                    key = (len(cov), -sum(math.dist((x, y), missing[n]) for n in cov))
                    if best_key is None or key > best_key:
                        best_key, best = key, (x, y, sorted(cov))
            y += step
        x += step
    return best
