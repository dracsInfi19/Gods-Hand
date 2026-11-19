import math
import random
import unittest

from sosmesh import filters, gps, priority
from sosmesh.clustering import dbscan
from sosmesh.gateway import Gateway
from sosmesh.link_quality import link_quality, link_quality_q8
from sosmesh.models import Heartbeat, LinkStats, SosPacket
from sosmesh.routing import best_alternate, best_relay_point, dijkstra
from sosmesh.watchdog import Watchdog, ACTIVE, STALE, LOST


def pkt(n=1, seq=1, t=0.0, kind="medical", x=100.0, y=100.0, **kw):
    lat, lon = gps.to_latlon(x, y)
    return SosPacket(n, seq, t, kind, lat, lon, **kw)


class LinkQualityTests(unittest.TestCase):
    def test_bounds(self):
        self.assertAlmostEqual(link_quality(10, -30, 1.0), 1.0)
        self.assertAlmostEqual(link_quality(-20, -120, 0.0), 0.0)

    def test_q8_matches_float(self):
        rng = random.Random(1)
        for _ in range(500):
            snr, rssi, d = rng.randint(-25, 15), rng.randint(-130, -20), rng.random()
            f = link_quality(snr, rssi, d)
            q = link_quality_q8(snr, rssi, round(d * 256)) / 256
            self.assertLess(abs(f - q), 0.02)


class PriorityTests(unittest.TestCase):
    def test_q8_matches_float(self):
        rng = random.Random(2)
        for _ in range(500):
            c = priority.components(rng.choice(list(priority.URGENCY)),
                                    frozenset(rng.sample(list(priority.VULN), rng.randint(0, 3))),
                                    rng.uniform(0, 100), rng.randint(1, 8),
                                    rng.uniform(0, 3000), rng.random() < 0.5)
            self.assertLessEqual(abs(priority.priority_float(c) - priority.priority_q8(c)), 1.0)

    def test_range_and_colors(self):
        top = priority.components("medical", frozenset({"child", "elderly"}), 0, 9, 9999, True)
        self.assertAlmostEqual(priority.priority_float(top), 100.0)
        self.assertEqual(priority.color(80), "red")
        self.assertEqual(priority.color(50), "orange")
        self.assertEqual(priority.color(49.9), "yellow")


class RoutingTests(unittest.TestCase):
    ADJ = {0: {1: 1, 2: 5}, 1: {0: 1, 3: 1}, 2: {0: 5, 3: 1}, 3: {1: 1, 2: 1}}

    def test_dijkstra_and_alternate(self):
        self.assertEqual(dijkstra(self.ADJ, 3, 0)[0], [3, 1, 0])
        alt = best_alternate(self.ADJ, 3, 0, failed_hop=1)
        self.assertEqual(alt[0], [3, 2, 0])

    def test_no_path(self):
        self.assertIsNone(best_alternate({0: {1: 1}, 1: {0: 1}}, 1, 0, failed_hop=0))

    def test_relay_covers_most(self):
        x, y, cov = best_relay_point({0: (0, 0)}, {1: (400, 0), 2: (420, 30), 3: (2000, 0)}, 450)
        self.assertEqual(cov, [1, 2])


class WatchdogTests(unittest.TestCase):
    def test_states(self):
        w = Watchdog(period=45)
        w.heard(1, 0)
        self.assertEqual(w.state(1, 100), ACTIVE)      # 2 missed
        self.assertEqual(w.state(1, 135), STALE)       # 3 missed
        self.assertEqual(w.state(1, 450), LOST)        # 10 missed


class GpsTests(unittest.TestCase):
    def test_roundtrip(self):
        x, y = gps.to_xy(*gps.to_latlon(123.0, -456.0))
        self.assertAlmostEqual(x, 123.0, places=3)
        self.assertAlmostEqual(y, -456.0, places=3)

    def test_kalman_reduces_jitter(self):
        rng, pf = random.Random(3), gps.PositionFilter()
        raw_err = filt_err = 0.0
        for i in range(40):
            mx, my = rng.gauss(50, 6), rng.gauss(50, 6)
            fx, fy = pf.update(mx, my, 1.5, i * 5.0)
            if i >= 10:
                raw_err += math.dist((mx, my), (50, 50))
                filt_err += math.dist((fx, fy), (50, 50))
        self.assertLess(filt_err, raw_err)

    def test_uncertainty_grows_without_fix(self):
        pf = gps.PositionFilter()
        pf.update(0, 0, 1.0, 0.0)
        self.assertGreater(pf.uncertainty(100), pf.uncertainty(10))

    def test_quality_gate(self):
        self.assertEqual(gps.gps_quality(6.0, 10), 0.0)
        self.assertGreater(gps.gps_quality(1.0, 12), 0.9)


class FilterTests(unittest.TestCase):
    def test_two_step_button(self):
        b = filters.TwoStepButton()
        b.press(0); self.assertFalse(b.release(1.0))      # short tap
        b.press(1.5); self.assertTrue(b.release(1.8))     # second quick tap
        b.press(10); self.assertTrue(b.release(13.1))     # 3 s hold

    def test_store_and_forward(self):
        buf = filters.NodeBuffer(retry_s=10)
        p = pkt(seq=7)
        buf.push(p, 0)
        self.assertEqual(len(buf.due(0)), 1)
        self.assertEqual(len(buf.due(5)), 0)
        self.assertEqual(len(buf.due(10)), 1)             # retry
        buf.ack(7)
        self.assertEqual(len(buf.due(100)), 0)

    def test_spam_filter(self):
        s = filters.SpamFilter(window=60, max_per_window=3)
        self.assertEqual(s.check(1, 1, 0), "OK")
        self.assertEqual(s.check(1, 1, 1), "RETRY")
        s.check(1, 2, 2); s.check(1, 3, 3)
        self.assertEqual(s.check(1, 4, 4), "RATE_LIMITED")
        self.assertEqual(s.check(1, 5, 100), "OK")         # new window

    def test_sanity(self):
        c = filters.SanityChecker(gps.DEFAULT_ORIGIN, zone_radius=2000)
        lat, lon = gps.to_latlon(10, 10)
        self.assertIsNone(c.check(1, lat, lon, 1.0, 8, 0))
        self.assertEqual(c.check(1, float("nan"), lon, 1.0, 8, 1), "invalid_coordinates")
        self.assertEqual(c.check(1, lat, lon, 7.0, 8, 2), "poor_gps_quality")
        far = gps.to_latlon(9000, 0)
        self.assertEqual(c.check(2, *far, 1.0, 8, 0), "outside_flood_zone")
        jump = gps.to_latlon(1500, 0)
        self.assertEqual(c.check(1, *jump, 1.0, 8, 3), "impossible_jump")

    def test_ladder(self):
        self.assertEqual(filters.decision(0.9), "DISPATCH")
        self.assertEqual(filters.decision(0.5), "VERIFY_WITH_DRONE")
        self.assertEqual(filters.decision(0.1), "UNVERIFIED")


class ClusterTests(unittest.TestCase):
    def test_dbscan(self):
        pts = [(0, 0), (10, 0), (5, 8), (500, 500), (1000, 0), (1010, 5)]
        lab = dbscan(pts, 40, 2)
        self.assertEqual(lab[0], lab[1]); self.assertEqual(lab[1], lab[2])
        self.assertEqual(lab[4], lab[5]); self.assertEqual(lab[3], -1)
        self.assertNotEqual(lab[0], lab[4])


class GatewayTests(unittest.TestCase):
    def test_sos_flow(self):
        gw = Gateway()
        self.assertEqual(gw.ingest_sos(pkt(seq=1), 0)["status"], "NEW")
        self.assertEqual(gw.ingest_sos(pkt(seq=1), 5)["status"], "RETRY")
        self.assertEqual(gw.sos[1].presses, 1)             # retry is not a new press
        self.assertEqual(gw.ingest_sos(pkt(seq=2), 10)["status"], "MERGED")
        self.assertEqual(gw.sos[1].presses, 2)
        self.assertEqual(gw.ingest_sos(pkt(seq=3, cancel=True), 15)["status"], "CANCELLED")
        self.assertNotIn(1, gw.sos)

    def test_bad_gps_never_drops_sos(self):
        gw = Gateway()
        r = gw.ingest_sos(pkt(hdop=9.0), 0)
        self.assertEqual(r["status"], "NEW")
        self.assertEqual(r["gps_issue"], "poor_gps_quality")
        self.assertIn(1, gw.sos)

    def test_drone_voting_and_match(self):
        gw = Gateway()
        gw.ingest_sos(pkt(x=100, y=100), 0)
        self.assertEqual(gw.ingest_detection(1, 103, 102, 0.7), "POSSIBLE")
        gw.ingest_detection(1, 103, 102, 0.7)
        self.assertEqual(gw.ingest_detection(1, 103, 102, 0.7), "CONFIRMED_NODE_MATCH")
        self.assertEqual(gw.sos[1].drone, "confirmed")
        self.assertEqual(gw.sos[1].pos, (103, 102))        # drone fix replaces node fix
        for _ in range(3):
            r = gw.ingest_detection(2, 900, 900, 0.9)
        self.assertEqual(r, "CONFIRMED_NO_NODE")

    def test_priority_rises_with_wait_and_low_battery(self):
        gw = Gateway()
        gw.ingest_sos(pkt(n=1, battery=90), 0)
        rec = gw.sos[1]
        self.assertGreater(gw.score(rec, 1000), gw.score(rec, 0))
        rec2 = gw.sos[1]
        gw.battery[1] = 5
        self.assertGreater(gw.score(rec2, 0), 0)

    def test_heartbeat_loss_changes_routes(self):
        gw = Gateway()
        def hb(n, t, nbrs):
            lat, lon = gps.to_latlon(n * 100.0, 0)
            return Heartbeat(n, t, 80, lat, lon, 1.0, 9, 0,
                             {m: LinkStats(-90, 0, 0.9) for m in nbrs})
        for t in (0, 45):
            gw.ingest_heartbeat(hb(1, t, [0, 2]), t)
            gw.ingest_heartbeat(hb(2, t, [1, 0]), t)
        self.assertEqual(gw.route(2, 45)[0], [2, 0])
        gw.ingest_heartbeat(hb(1, 90, [0, 2]), 90)         # node 2 goes silent
        self.assertEqual(gw.node_states(90 + 135)[2], STALE)
        self.assertNotIn(2, gw.alive(90 + 135))


if __name__ == "__main__":
    unittest.main()
