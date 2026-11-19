"""Link quality score:  LQ = 0.5*norm(SNR) + 0.3*norm(RSSI) + 0.2*delivery

Float reference + integer (Q8) version. The Q8 version is the bit-accurate
target for firmware / RTL: all inputs/outputs are small integers, no division.
"""
SNR_MIN, SNR_MAX = -20.0, 10.0
RSSI_MIN, RSSI_MAX = -120.0, -30.0
W_SNR, W_RSSI, W_DEL = 0.5, 0.3, 0.2
MIN_USABLE_LQ = 0.10


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def norm(v: float, lo: float, hi: float) -> float:
    return clamp01((v - lo) / (hi - lo))


def link_quality(snr: float, rssi: float, delivery: float) -> float:
    return W_SNR * norm(snr, SNR_MIN, SNR_MAX) \
        + W_RSSI * norm(rssi, RSSI_MIN, RSSI_MAX) \
        + W_DEL * clamp01(delivery)


def edge_cost(lq: float) -> float:
    """Dijkstra edge cost = 1/LQ (weak links are expensive)."""
    return 1.0 / max(lq, 0.01)


# ---- fixed point (Q8: 256 == 1.0) -----------------------------------------
def _norm_q8(v: int, lo: int, hi: int) -> int:
    x = ((v - lo) * 256) // (hi - lo)
    return 0 if x < 0 else 256 if x > 256 else x


def link_quality_q8(snr_db: int, rssi_dbm: int, delivery_q8: int) -> int:
    """Integer inputs -> LQ in Q8 (0..256). Weights 128+77+51 = 256."""
    s = _norm_q8(snr_db, int(SNR_MIN), int(SNR_MAX))
    r = _norm_q8(rssi_dbm, int(RSSI_MIN), int(RSSI_MAX))
    d = 0 if delivery_q8 < 0 else 256 if delivery_q8 > 256 else delivery_q8
    return (128 * s + 77 * r + 51 * d) >> 8
