"""Behavioural distance over action traces — the manipulation-check ruler.

Reads the ordered tool stream from an action trace and compares two streams at three
pre-committed levels: unigram Jensen-Shannon over tool names (frequency), bigram JS
(order), and normalized compression distance over the raw sequence (procedure). The
between/within ratio `R` with a label-permutation test is the action-preference statistic:
does *who is acting* explain more of the spread than chance relabelling does.

This is what makes the paper's Step-1 (does the knob turn?) and δ₀ (dose) computable, and
it is read from the same normalized `ActionEvent` trace any harness emits.
"""

from __future__ import annotations

import bz2
import itertools
import math
import random
from collections import Counter
from typing import Sequence

from proteus.core.adapter import ActionEvent

TEXT_TOKEN = "<text>"


def tool_stream(trace: Sequence[ActionEvent]) -> list[str]:
    return [e.tool or TEXT_TOKEN for e in trace]


def _js(p: Counter, q: Counter) -> float:
    keys = set(p) | set(q)
    tp, tq = sum(p.values()) or 1, sum(q.values()) or 1
    m = {k: 0.5 * (p[k] / tp + q[k] / tq) for k in keys}

    def kl(a: Counter, ta: int) -> float:
        s = 0.0
        for k in keys:
            pa = a[k] / ta
            if pa > 0:
                s += pa * math.log2(pa / m[k])
        return s
    return 0.5 * kl(p, tp) + 0.5 * kl(q, tq)


def freq_distance(a: Sequence[str], b: Sequence[str]) -> float:
    return _js(Counter(a), Counter(b))


def order_distance(a: Sequence[str], b: Sequence[str]) -> float:
    ba = Counter(zip(a, a[1:]))
    bb = Counter(zip(b, b[1:]))
    return _js(ba, bb)


def ncd(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = (" ".join(a)).encode(), (" ".join(b)).encode()
    ca, cb = len(bz2.compress(sa)), len(bz2.compress(sb))
    cab = len(bz2.compress(sa + b" " + sb))
    return (cab - min(ca, cb)) / max(ca, cb) if max(ca, cb) else 0.0


def between_within(streams: dict[str, list[list[str]]], level: str = "freq",
                   permutations: int = 10000, seed: int = 0) -> dict:
    """R = mean between-label distance / mean within-label distance, with a permutation p.

    `streams` maps a label (condition/seed) to its list of tool streams. The distance
    matrix is computed once; only the labels are shuffled, so the null tested is exactly
    'the labels are exchangeable'.
    """
    dist = {"freq": freq_distance, "order": order_distance, "ncd": ncd}[level]
    items = [(lbl, s) for lbl, ss in streams.items() for s in ss]
    n = len(items)
    D = [[0.0] * n for _ in range(n)]
    for i, j in itertools.combinations(range(n), 2):
        D[i][j] = D[j][i] = dist(items[i][1], items[j][1])
    labels = [lbl for lbl, _ in items]

    def ratio(labs: list[str]) -> float:
        wi = [D[i][j] for i, j in itertools.combinations(range(n), 2) if labs[i] == labs[j]]
        bw = [D[i][j] for i, j in itertools.combinations(range(n), 2) if labs[i] != labs[j]]
        mw = sum(wi) / len(wi) if wi else 1e-9
        mb = sum(bw) / len(bw) if bw else 0.0
        return (mb / mw) if mw else 0.0, mw, mb

    r_obs, mw, mb = ratio(labels)
    rng = random.Random(seed)
    hits = 0
    for _ in range(permutations):
        shuf = labels[:]
        rng.shuffle(shuf)
        r, _, _ = ratio(shuf)
        if r >= r_obs:
            hits += 1
    return {"R": r_obs, "within": mw, "between": mb,
            "p": (hits + 1) / (permutations + 1), "level": level}
