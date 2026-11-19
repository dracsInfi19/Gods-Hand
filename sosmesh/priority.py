"""SOS priority 0..100.

Priority = 30*urgency + 20*vulnerability + 15*battery_risk
         + 15*repeat_presses + 10*wait_time + 10*drone_confirmed

Float reference + integer Q8 version (bit-accurate target for firmware / RTL).
"""
from typing import FrozenSet

URGENCY = {"medical": 1.0, "trapped": 0.8, "supplies": 0.4}
VULN = {"child": 0.5, "elderly": 0.5, "injured": 0.5}
WEIGHTS = (30, 20, 15, 15, 10, 10)
WAIT_CAP_S = 1800.0
REPEAT_CAP = 5


def components(kind: str, flags: FrozenSet[str], battery: float, presses: int,
               age_s: float, drone_confirmed: bool):
    urgency = URGENCY.get(kind, 0.4)
    vuln = min(1.0, sum(VULN.get(f, 0.0) for f in flags))
    batt = max(0.0, min(1.0, 1.0 - battery / 100.0))
    rep = min(presses, REPEAT_CAP) / REPEAT_CAP
    wait = max(0.0, min(1.0, age_s / WAIT_CAP_S))
    return (urgency, vuln, batt, rep, wait, 1.0 if drone_confirmed else 0.0)


def priority_float(*comp) -> float:
    c = comp[0] if len(comp) == 1 else comp
    return sum(w * v for w, v in zip(WEIGHTS, c))


def priority_q8(comp) -> int:
    """Components -> Q8 ints, MAC, round-to-nearest. Returns int 0..100."""
    q = [int(round(v * 256)) for v in comp]
    return (sum(w * v for w, v in zip(WEIGHTS, q)) + 128) >> 8


def color(score: float) -> str:
    return "red" if score >= 80 else "orange" if score >= 50 else "yellow"
