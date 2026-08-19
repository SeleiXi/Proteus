"""Benchmarks as goals: task packs whose graders are Proteus evaluators."""
from proteus.bench.task import (
    TASK_SUBDIR,
    BenchTask,
    as_evaluator,
    as_goal,
    seed_task,
    task_root,
    workspace_diff,
)

__all__ = [
    "TASK_SUBDIR",
    "BenchTask",
    "as_evaluator",
    "as_goal",
    "seed_task",
    "task_root",
    "workspace_diff",
]
