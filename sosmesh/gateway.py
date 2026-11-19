"""Gateway: ties every block together and produces a dashboard snapshot."""
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from . import filters, gps, priority
from .clustering import dbscan
from .models import Heartbeat, SosPacket
from .routing import MeshGraph, best_alternate, best_relay_point, dijkstra, reachable
from .watchdog import ACTIVE, Watchdog


@dataclass
class Config:
    gateway_id: int = 0
    hb_period: float = 45.0
    radio_range: float = 450.0
    zone_radius: float = 5000.0
    cluster_eps: float = 40.0
    match_radius: float = 20.0
    origin: Tuple[float, float] = gps.DEFAULT_ORIGIN


@dataclass
class SosRecord:
    node_id: int
    kind: str
    flags: FrozenSet[str]
    first_ts: float
    last_ts: float
    battery: float
    motion: str
    presses: int = 1
    pos: Optional[Tuple[float, float]] = None
    pos_trusted: bool = False
    gps_q: float = 0.0
    history: List[Tuple[float, float]] = field(default_factory=list)
    drone: str = "none"                 # none | possible | confirmed


class MultiFrameVoter:
    """Confirm a track only if detected at >= conf in k of the last n frames."""

    def __init__(self, k: int = 3, n: int = 5):
        self.k, self.n, self.hist = k, n, {}

    def update(self, track_id: int, hit: bool) -> bool:
        dq = self.hist.setdefault(track_id, deque(maxlen=self.n))
        dq.append(hit)
        return sum(dq) >= self.k


class Gateway:
    def __init__(self, cfg: Optional[Config] = None):
        self.cfg = cfg or Config()
        self.graph = MeshGraph()
        self.dog = Watchdog(self.cfg.hb_period)
        self.spam = filters.SpamFilter()
        self.sanity = filters.SanityChecker(self.cfg.origin, zone_radius=self.cfg.zone_radius)
        self.pos_filters: Dict[int, gps.PositionFilter] = {}
        self.battery: Dict[int, float] = {}
        self.sos: Dict[int, SosRecord] = {}
        self.voter = MultiFrameVoter()
        self.unmatched: Dict[int, Tuple[float, float]] = {}

    # ---- heartbeats / topology -------------------------------------------
    def ingest_heartbeat(self, hb: Heartbeat, now: float) -> None:
        x, y = gps.to_xy(hb.lat, hb.lon, self.cfg.origin)
        pf = self.pos_filters.setdefault(hb.node_id, gps.PositionFilter())
        if gps.fix_ok(hb.hdop, hb.sats):
            pf.update(x, y, hb.hdop, hb.ts)
        self.battery[hb.node_id] = hb.battery
        self.dog.heard(hb.node_id, now, pf.position())
        self.graph.update(hb)

    def node_states(self, now: float) -> Dict[int, str]:
        return self.dog.states(now)

    def alive(self, now: float) -> set:
        a = {n for n, s in self.node_states(now).items() if s == ACTIVE}
        a.add(self.cfg.gateway_id)
        return a

    def adjacency(self, now: float):
        return self.graph.adjacency(self.alive(now))

    def route(self, node_id: int, now: float):
        return dijkstra(self.adjacency(now), node_id, self.cfg.gateway_id)

    def alternate(self, node_id: int, failed_hop: int, now: float):
        return best_alternate(self.adjacency(now), node_id, self.cfg.gateway_id, failed_hop)

    def relay_suggestion(self, now: float):
        """Where to send the drone as a temporary relay for cut-off nodes."""
        gid = self.cfg.gateway_id
        states = self.node_states(now)
        reach = reachable(self.adjacency(now), gid)
        connected = {gid: (0.0, 0.0)}
        missing = {}
        for n, s in states.items():
            p = self.dog.last_pos.get(n)
            if p is None:
                continue
            if n in reach:
                connected[n] = p
            else:
                missing[n] = p
        return best_relay_point(connected, missing, self.cfg.radio_range)

    # ---- SOS ---------------------------------------------------------------
    def ingest_sos(self, pkt: SosPacket, now: float) -> dict:
        n = pkt.node_id
        if pkt.cancel:
            self.sos.pop(n, None)
            return {"status": "CANCELLED", "ack": pkt.seq}
        verdict = self.spam.check(n, pkt.seq, now)
        if verdict != "OK":
            return {"status": verdict, "ack": pkt.seq}   # ACK so the node stops retrying

        # --- location: trust the fix only if it passes every sanity check
        reason = self.sanity.check(n, pkt.lat, pkt.lon, pkt.hdop, pkt.sats, pkt.ts)
        pf = self.pos_filters.setdefault(n, gps.PositionFilter())
        if reason is None:
            x, y = gps.to_xy(pkt.lat, pkt.lon, self.cfg.origin)
            pf.update(x, y, pkt.hdop, pkt.ts)
            pos, gq = pf.position(), gps.gps_quality(pkt.hdop, pkt.sats)
        else:                         # never drop an SOS for bad GPS: fall back, lower confidence
            pos, gq = pf.position() or self.dog.last_pos.get(n), 0.0

        rec = self.sos.get(n)
        if rec is None:
            rec = self.sos[n] = SosRecord(n, pkt.kind, pkt.flags, now, now, pkt.battery, pkt.motion)
            status = "NEW"
        else:
            rec.presses += 1
            rec.flags = rec.flags | pkt.flags
            rec.last_ts, rec.motion = now, pkt.motion
            status = "MERGED"
        self.battery[n] = min(self.battery.get(n, 100.0), pkt.battery)
        rec.pos, rec.pos_trusted, rec.gps_q = pos, reason is None, gq
        if reason is None and pos is not None:
            rec.history.append(pos)
        return {"status": status, "ack": pkt.seq, "gps_issue": reason}

    # ---- drone computer vision ---------------------------------------------
    def ingest_detection(self, track_id: int, x: float, y: float, conf: float) -> str:
        """One YOLO detection of a person (tracked id, local metres, confidence)."""
        confirmed = self.voter.update(track_id, conf >= 0.6)
        if not confirmed:
            return "POSSIBLE" if conf >= 0.4 else "IGNORED"
        best, bd = None, self.cfg.match_radius
        for rec in self.sos.values():
            if rec.pos is not None and math.dist(rec.pos, (x, y)) <= bd:
                best, bd = rec, math.dist(rec.pos, (x, y))
        if best is None:
            self.unmatched[track_id] = (x, y)
            return "CONFIRMED_NO_NODE"
        best.drone, best.pos, best.pos_trusted = "confirmed", (x, y), True   # drone fix is more exact
        return "CONFIRMED_NODE_MATCH"

    # ---- scoring -----------------------------------------------------------
    def repeat_consistency(self, rec: SosRecord) -> float:
        if len(rec.history) < 2:
            return 0.5
        cx = sum(p[0] for p in rec.history) / len(rec.history)
        cy = sum(p[1] for p in rec.history) / len(rec.history)
        return 1.0 if max(math.dist(p, (cx, cy)) for p in rec.history) <= 15.0 else 0.3

    def score(self, rec: SosRecord, now: float) -> float:
        batt = self.battery.get(rec.node_id, rec.battery)
        comp = priority.components(rec.kind, rec.flags, batt, rec.presses,
                                   now - rec.first_ts, rec.drone == "confirmed")
        return priority.priority_float(comp)

    def conf(self, rec: SosRecord) -> float:
        cv = 1.0 if rec.drone == "confirmed" else 0.5 if rec.drone == "possible" else 0.0
        return filters.confidence(rec.gps_q, self.repeat_consistency(rec), rec.motion, cv)

    def ranked(self, now: float):
        return sorted(self.sos.values(), key=lambda r: -self.score(r, now))

    def clusters(self, now: float):
        recs = [r for r in self.sos.values() if r.pos is not None]
        labels = dbscan([r.pos for r in recs], self.cfg.cluster_eps, 2)
        groups: Dict[int, list] = {}
        nxt = max(labels, default=-1) + 1
        for r, l in zip(recs, labels):
            if l == -1:
                l, nxt = nxt, nxt + 1        # noise point = cluster of one
            groups.setdefault(l, []).append(r)
        out = []
        for g in groups.values():
            top = max(self.score(r, now) for r in g)
            out.append({"people": len(g), "nodes": sorted(r.node_id for r in g),
                        "centroid": (round(sum(r.pos[0] for r in g) / len(g), 1),
                                     round(sum(r.pos[1] for r in g) / len(g), 1)),
                        "max_priority": round(top, 1), "color": priority.color(top)})
        return sorted(out, key=lambda c: -c["max_priority"])

    # ---- dashboard snapshot --------------------------------------------------
    def snapshot(self, now: float) -> dict:
        states = self.node_states(now)
        nodes = []
        for n, s in sorted(states.items()):
            pf = self.pos_filters.get(n)
            p = self.dog.last_pos.get(n)
            nodes.append({"id": n, "state": s, "color": "green" if s == ACTIVE else "gray",
                          "pos": None if p is None else (round(p[0], 1), round(p[1], 1)),
                          "battery": self.battery.get(n),
                          "uncertainty_m": None if pf is None else round(pf.uncertainty(now), 1)})
        adj = self.adjacency(now)
        links = [[u, v, round(1 / c, 2)] for u, nb in adj.items() for v, c in nb.items() if u < v]
        routes = {}
        for n in states:
            r = dijkstra(adj, n, self.cfg.gateway_id) if states[n] == ACTIVE else None
            routes[n] = None if r is None else {"path": r[0], "cost": round(r[1], 2)}
        sos = []
        for r in self.ranked(now):
            sc, cf = self.score(r, now), self.conf(r)
            sos.append({"node": r.node_id, "kind": r.kind, "flags": sorted(r.flags),
                        "priority": round(sc, 1), "color": priority.color(sc),
                        "confidence": round(cf, 2), "decision": filters.decision(cf),
                        "pos": None if r.pos is None else (round(r.pos[0], 1), round(r.pos[1], 1)),
                        "presses": r.presses, "drone": r.drone, "battery": round(self.battery.get(r.node_id, r.battery)),
                        "motion": r.motion, "trusted": r.pos_trusted,
                        "age_s": round(now - r.first_ts)})
        return {"t": now, "nodes": nodes, "routes": routes, "sos": sos,
                "links": links, "clusters": self.clusters(now), "relay": self.relay_suggestion(now),
                "unmatched_survivors": {k: tuple(round(c, 1) for c in v) for k, v in self.unmatched.items()}}
