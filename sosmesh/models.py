from dataclasses import dataclass, field
from typing import Dict, FrozenSet


@dataclass(frozen=True)
class LinkStats:
    rssi: float       # dBm
    snr: float        # dB
    delivery: float   # 0..1 packet delivery ratio on this link


@dataclass
class Heartbeat:
    node_id: int
    ts: float
    battery: float                      # percent 0..100
    lat: float
    lon: float
    hdop: float = 1.0
    sats: int = 8
    hop_count: int = 0
    neighbors: Dict[int, LinkStats] = field(default_factory=dict)


@dataclass
class SosPacket:
    node_id: int
    seq: int                            # same seq = store-and-forward retry
    ts: float
    kind: str                           # "medical" | "trapped" | "supplies"
    lat: float
    lon: float
    hdop: float = 1.0
    sats: int = 8
    battery: float = 100.0
    flags: FrozenSet[str] = frozenset() # {"child","elderly","injured"}
    motion: str = "moving"              # "moving" | "still" | "dropped"
    cancel: bool = False
