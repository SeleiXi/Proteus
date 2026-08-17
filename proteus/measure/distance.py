"""Structural distance between harness states — the instrument no fixed harness-evolver ships.

Distance is defined over **units of structure**, not bytes (a byte count measures
verbosity), and separately per **surface**, iterating the harness's declared manifest so a
new harness with new surfaces is measured without changing this file. For each surface a
unit is added / dropped / revised / unchanged between two states; these are reported
separately (a harness that only adds is accumulating; one that revises and drops is
curating) and their sum over the union is the distance in [0, 1].

Travel is the **path length** summed episode to episode, not endpoint displacement, because
surfaces start empty and endpoint distance saturates after the first episode.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from proteus.core.adapter import Surface


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def units(harness_root: Path, surfaces: Sequence[Surface]) -> dict[str, dict[str, str]]:
    """Every unit of every declared surface, as {surface: {unit_id: content_hash}}."""
    out: dict[str, dict[str, str]] = {s.name: {} for s in surfaces}
    for s in surfaces:
        base = harness_root / s.subdir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name.startswith(".") or "__pycache__" in path.parts:
                continue
            # a "directory"-unit surface (e.g. skills) keys by the containing dir; a
            # "file"-unit surface keys by the file stem.
            name = path.parent.name if s.unit == "directory" else path.stem
            out[s.name][name] = _sha(_read(path))
    return out


@dataclass(frozen=True)
class SurfaceDelta:
    surface: str
    added: int
    dropped: int
    revised: int
    unchanged: int

    @property
    def union(self) -> int:
        return self.added + self.dropped + self.revised + self.unchanged

    @property
    def distance(self) -> float:
        return 0.0 if not self.union else (self.added + self.dropped + self.revised) / self.union

    @property
    def churn(self) -> float:
        """Of what moved, the fraction that was revision/removal rather than accumulation."""
        moved = self.added + self.dropped + self.revised
        return 0.0 if not moved else (self.dropped + self.revised) / moved


def _sim(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9_]{3,}", a.lower()))
    tb = set(re.findall(r"[a-z0-9_]{3,}", b.lower()))
    if not ta or not tb:
        return 1.0 if ta == tb else 0.0
    return len(ta & tb) / len(ta | tb)


def delta(a: dict[str, str], b: dict[str, str], surface: str) -> SurfaceDelta:
    both = set(a) & set(b)
    revised = sum(1 for k in both if a[k] != b[k])
    return SurfaceDelta(surface, len(set(b) - set(a)), len(set(a) - set(b)),
                        revised, len(both) - revised)


def compare(root_a: Path, root_b: Path, surfaces: Sequence[Surface]) -> dict[str, SurfaceDelta]:
    ua, ub = units(root_a, surfaces), units(root_b, surfaces)
    return {s.name: delta(ua[s.name], ub[s.name], s.name) for s in surfaces}


def path_length(states: Sequence[Path], surfaces: Sequence[Surface]) -> dict[str, SurfaceDelta]:
    """Travel summed along consecutive states, per surface."""
    totals = {s.name: SurfaceDelta(s.name, 0, 0, 0, 0) for s in surfaces}
    for before, after in zip(states, states[1:]):
        step = compare(before, after, surfaces)
        for name, d in step.items():
            acc = totals[name]
            totals[name] = SurfaceDelta(name, acc.added + d.added, acc.dropped + d.dropped,
                                        acc.revised + d.revised, acc.unchanged + d.unchanged)
    return totals
