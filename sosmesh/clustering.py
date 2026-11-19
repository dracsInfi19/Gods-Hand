"""DBSCAN so rescuers see '5 people in one area' instead of 5 pins."""
import math
from typing import List, Sequence, Tuple


def dbscan(pts: Sequence[Tuple[float, float]], eps: float, min_samples: int) -> List[int]:
    n = len(pts)
    labels = [None] * n

    def nbrs(i):
        return [j for j in range(n) if math.dist(pts[i], pts[j]) <= eps]

    cid = 0
    for i in range(n):
        if labels[i] is not None:
            continue
        nb = nbrs(i)
        if len(nb) < min_samples:
            labels[i] = -1
            continue
        labels[i] = cid
        seeds = [j for j in nb if j != i]
        while seeds:
            j = seeds.pop()
            if labels[j] == -1:
                labels[j] = cid
            if labels[j] is not None:
                continue
            labels[j] = cid
            nj = nbrs(j)
            if len(nj) >= min_samples:
                seeds.extend(nj)
        cid += 1
    return labels
