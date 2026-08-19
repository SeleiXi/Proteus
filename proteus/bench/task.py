"""Benchmark tasks as goals: the agent solves a task, the evaluator scores what it wrote.

A goal-conditioned run needs two workspaces that must not be confused:

* the **harness** (`<run>/harness/`) — what evolves and what we measure; it persists
  across episodes and is the dependent variable;
* the **task workspace** (`<run>/harness/task/`) — the thing the agent is asked to work
  on this episode.

Putting the task *inside* the harness workspace is deliberate: every adapter already gives
the agent file access to its own workspace, so a benchmark plugs in with no adapter change
at all. The cost is that task files count as harness structure unless the measurement layer
excludes them, which `TASK_SUBDIR` exists to make explicit.

A `BenchTask` is three things: text the agent is given as its goal, a setup that seeds the
task workspace, and a grader that reads the workspace afterwards. Everything else — when
the goal text is shown, whether the score is visible to the agent, whether a regression is
rejected — is already the framework's job (`GoalConfig`).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from proteus.core.adapter import ActionEvent
from proteus.core.goal import EvalResult, Goal, GoalConfig, GoalContext, Visibility

#: Where a task is seeded, relative to the harness root.
TASK_SUBDIR = "task"


def task_root(harness_root: str | Path) -> Path:
    return Path(harness_root) / TASK_SUBDIR


@dataclass(frozen=True)
class BenchTask:
    """One benchmark instance, expressed in the three things a goal run needs."""

    id: str
    goal_text: str
    setup: Callable[[Path], None]
    grade: Callable[[Path], EvalResult]
    #: git ref the task workspace starts from, when the grader needs a diff
    base_commit: str = ""


def workspace_diff(ws: Path, base: str = "") -> str:
    """The agent's work as a git diff — the only thing most graders can consume.

    Falls back to a diff against the seeded state when no base commit is given (the local
    task pack commits its seed, so `HEAD` is that state).
    """
    ws = Path(ws)
    if not (ws / ".git").exists():
        return ""
    subprocess.run(["git", "-C", str(ws), "add", "-A"], capture_output=True, check=False)
    ref = base or "HEAD"
    out = subprocess.run(["git", "-C", str(ws), "diff", "--cached", ref],
                         capture_output=True, text=True, check=False)
    return out.stdout


def as_evaluator(task: BenchTask):
    """Wrap a task's grader in the framework's evaluator signature."""

    def evaluate(trace: Sequence[ActionEvent], ctx: GoalContext) -> EvalResult:
        ws = task_root(ctx.harness_root)
        if not ws.exists():
            return EvalResult(name=task.id, score=0.0, passed=False,
                              detail="task workspace missing (was setup run?)")
        return task.grade(ws)

    return evaluate


def as_goal(task: BenchTask, *, visibility: Visibility = Visibility.HIDDEN,
            selection: str = "none") -> GoalConfig:
    """A single-goal config for this task, ready to hand to `RunConfig`."""
    return GoalConfig.single(
        Goal(name=task.id, text=task.goal_text, evaluator=as_evaluator(task),
             visibility=visibility),
        selection=selection,
    )


def seed_task(harness_root: str | Path, task: BenchTask) -> Path:
    """Materialise the task workspace. Call once, before the first episode."""
    ws = task_root(harness_root)
    ws.mkdir(parents=True, exist_ok=True)
    task.setup(ws)
    return ws
