"""End-to-end demo: python -m sosmesh.simulator"""
import json
import math
import random

from .gateway import Gateway
from .gps import to_latlon, DEFAULT_ORIGIN
from .models import Heartbeat, LinkStats, SosPacket

RANGE = 450.0
POS = {0: (0, 0), 1: (280, 60), 2: (300, -200), 3: (600, -50), 4: (620, 220),
       5: (900, 0), 6: (1250, 100), 7: (1300, -150), 8: (-250, 250),
       9: (-200, -300), 10: (-232, 268), 11: (-262, 232)}
PERIOD = 45


def link(d, rng):
    return LinkStats(rssi=-40 - 30 * math.log10(max(d, 1)) + rng.gauss(0, 1),
                     snr=10 - d / 20 + rng.gauss(0, 0.5),
                     delivery=max(0.0, min(1.0, 1 - d / 500 + rng.gauss(0, 0.02))))


def world_reach(alive):
    seen, stack = {0}, [0]
    while stack:
        u = stack.pop()
        for v in alive:
            if v not in seen and math.dist(POS[u], POS[v]) <= RANGE:
                seen.add(v)
                stack.append(v)
    return seen


def sos(n, seq, t, kind, flags=(), batt=60, hdop=1.5, motion="moving", rng=None, cancel=False):
    x, y = POS[n]
    x, y = x + rng.gauss(0, 4), y + rng.gauss(0, 4)
    lat, lon = to_latlon(x, y)
    return SosPacket(n, seq, t, kind, lat, lon, hdop, 9, batt, frozenset(flags), motion, cancel)


def show_rank(gw, t, title):
    print(f"\n--- {title} (t={t}s) ---")
    for s in gw.snapshot(t)["sos"]:
        print(f"  node {s['node']:>2}  P={s['priority']:5.1f} {s['color']:<6} conf={s['confidence']:.2f} "
              f"{s['decision']:<17} {s['kind']:<8} {s['flags']} presses={s['presses']} drone={s['drone']}")
    for c in gw.snapshot(t)["clusters"]:
        if c["people"] > 1:
            print(f"  CLUSTER: {c['people']} people near {c['centroid']} nodes={c['nodes']} -> {c['color']}")


def main():
    rng = random.Random(7)
    gw = Gateway()
    alive = set(range(1, 12))
    batt = {n: 100.0 for n in alive}
    batt[5] = 40.0
    prev_state = {}
    frames = []
    for t in range(0, 1200, PERIOD):
        # ---- heartbeats (delivered only if the node can reach the gateway)
        reach = world_reach(alive)
        for n in sorted(alive & reach):
            batt[n] = max(0.0, batt[n] - (0.9 if n == 5 else 0.3))
            nb = {m: link(math.dist(POS[n], POS[m]), rng) for m in list(alive) + [0]
                  if m != n and math.dist(POS[n], POS[m]) <= RANGE}
            lat, lon = to_latlon(*POS[n])
            gw.ingest_heartbeat(Heartbeat(n, t, batt[n], lat, lon, 1.2, 9, 0, nb), t)

        if t == 90:
            print("=== routes (cost = sum of 1/LQ) ===")
            for n in range(1, 12):
                r = gw.route(n, t)
                print(f"  node {n:>2}: path={r[0]} cost={r[1]:.2f}")
            print("\n=== SOS wave ===")
            pk = [sos(5, 1, t, "medical", {"child", "injured"}, 22, rng=rng),
                  sos(8, 1, t, "trapped", rng=rng), sos(10, 1, t, "trapped", {"elderly"}, rng=rng),
                  sos(11, 1, t, "supplies", rng=rng), sos(9, 1, t, "supplies", rng=rng),
                  sos(2, 1, t, "medical", {"elderly"}, rng=rng),
                  sos(6, 1, t, "trapped", hdop=9.0, rng=rng),          # bad GPS
                  sos(1, 1, t, "medical", rng=rng)]
            for p in pk:
                print(f"  node {p.node_id:>2} -> {gw.ingest_sos(p, t)}")
            print("  node  5 retry (same seq)   ->", gw.ingest_sos(pk[0], t + 10))
            print("  node  5 second press       ->", gw.ingest_sos(sos(5, 2, t + 20, "medical", rng=rng), t + 20))
            print("  node  1 cancel (accidental)->", gw.ingest_sos(sos(1, 2, t + 25, "medical", rng=rng, cancel=True), t + 25))
            out = [gw.ingest_sos(sos(7, s, t + s, "medical", rng=rng), t + s)["status"] for s in range(1, 12)]
            print("  node  7 spam burst         ->", out)
        if t == 135:
            show_rank(gw, t, "ranking before drone")
            print("\n=== drone detections ===")
            x5, y5 = POS[5]
            for conf in (0.5, 0.7, 0.8, 0.65):
                print("  track 101 conf", conf, "->", gw.ingest_detection(101, x5 + 8, y5 + 3, conf))
            for conf in (0.5, 0.55):
                print("  track 102 conf", conf, "->", gw.ingest_detection(102, -200, -300, conf))
            for conf in (0.7, 0.8, 0.9):
                last = gw.ingest_detection(103, 400, -600, conf)
            print("  track 103 (no node nearby) ->", last)
            show_rank(gw, t, "ranking after drone")
        if t == 540:
            show_rank(gw, t, "ranking after battery drain + waiting")
        if t == 585:
            alive.discard(3)
            print("\n=== node 3 dies (t=585) ===")
            print("  node 5 ACK timeout via 3 -> alternate:", gw.alternate(5, 3, t))
        if t == 810:
            alive.discard(4)
            print("\n=== node 4 dies (t=810): nodes 5,6,7 are cut off ===")
        cur = gw.node_states(t)
        for n, s in cur.items():
            if prev_state.get(n) not in (None, s):
                print(f"  t={t}: node {n} {prev_state[n]} -> {s}")
        prev_state = cur
        frames.append(gw.snapshot(t))
    snap = gw.snapshot(1155)
    print("\n=== final ===")
    print("  gap healing, send drone relay to:", snap["relay"])
    print("  gray nodes:", [(n["id"], n["pos"], n["uncertainty_m"]) for n in snap["nodes"] if n["color"] == "gray"])
    print("  unmatched survivors (drone saw, no node):", snap["unmatched_survivors"])
    with open("dashboard/frames.json", "w") as f:
        json.dump(frames, f, default=str)
    print("  wrote dashboard/frames.json (%d frames)" % len(frames))


if __name__ == "__main__":
    main()
