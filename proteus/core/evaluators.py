"""Built-in evaluators — small, offline, and composable.

An `Evaluator` is any callable `(trace, ctx) -> EvalResult`; these cover the common cases
so tests, examples, and quick experiments need no custom code. Benchmark verifiers and LLM
judges plug in the same way — write a callable, put it on a `Goal`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from proteus.core.adapter import ActionEvent
from proteus.core.goal import EvalResult, GoalContext


def surface_units(surface_subdir: str, name: str = "", target: int = 0):
    """Score = number of units on a surface (files under `harness/<surface_subdir>`).

    An intrinsic, harness-readable measure: no model, no network. With `target`, score is
    capped there and `passed` flips when reached.
    """
    label = name or f"units:{surface_subdir}"

    def evaluate(trace: Sequence[ActionEvent], ctx: GoalContext) -> EvalResult:
        root = Path(ctx.harness_root) / surface_subdir
        n = sum(1 for p in root.rglob("*") if p.is_file()) if root.exists() else 0
        score = float(min(n, target) if target else n)
        return EvalResult(name=label, score=score,
                          passed=bool(target and n >= target),
                          detail=f"{n} files on {surface_subdir}/")
    return evaluate


def tool_calls(name: str = "tool-calls", ceiling: int = 0):
    """Score = number of tool calls in the episode's trace (activity, not quality).

    With `ceiling`, score is `ceiling - calls` — an economy objective: fewer calls score
    higher, which is the shape reward-hacking studies need.
    """

    def evaluate(trace: Sequence[ActionEvent], ctx: GoalContext) -> EvalResult:
        calls = sum(1 for e in trace if e.tool)
        score = float(ceiling - calls) if ceiling else float(calls)
        return EvalResult(name=name, score=score, detail=f"{calls} tool calls")
    return evaluate


def file_contains(relpath: str, needle: str, name: str = ""):
    """Pass/fail: does `harness/<relpath>` exist and contain `needle`? A minimal task
    verifier — the shape a benchmark check takes."""
    label = name or f"contains:{relpath}"

    def evaluate(trace: Sequence[ActionEvent], ctx: GoalContext) -> EvalResult:
        root = Path(ctx.harness_root).resolve()
        p = (root / relpath).resolve()
        # confine to the harness root: relpath is caller-supplied and, on the hosted lab,
        # attacker-supplied — an absolute or ../ path would turn this into a file-read
        # oracle over the host (existence + substring membership via the returned score).
        if not (p == root or root in p.parents):
            return EvalResult(name=label, score=0.0, passed=False,
                              detail=f"{relpath} escapes the harness root")
        ok = p.is_file() and needle in p.read_text(encoding="utf-8", errors="replace")
        return EvalResult(name=label, score=1.0 if ok else 0.0, passed=ok,
                          detail=f"{relpath} {'contains' if ok else 'missing'} target")
    return evaluate
