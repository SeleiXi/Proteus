"""SWE-bench as a goal: the agent fixes a real issue, the official harness grades the patch.

This is the high-fidelity end of the goal axis. One task = one SWE-bench instance; the
agent's `task/` workspace is that repository checked out at `base_commit`, its goal text is
the issue's `problem_statement`, and grading runs the instance's own container: apply the
agent's diff, apply the held-out `test_patch`, run `FAIL_TO_PASS` and `PASS_TO_PASS`.

Three facts decide whether this works, all of them load-bearing:

1. **The bridge is the diff.** The grader has its own clean checkout inside the image; the
   only thing that crosses is `git diff base_commit`. If the task workspace is not that
   repository at exactly that commit, every apply strategy fails and the score is zero.
   `setup` therefore clones and checks out; do not point this at an arbitrary directory.
2. **Cache keys ignore the patch.** The official harness caches on `(run_id, instance_id)`,
   so a stale result comes back if the run id repeats. The run id here embeds the episode.
3. **This is not a laptop workload.** Per-instance images are pulled from Docker Hub
   (hundreds of MB each over a shared base); the project asks for ~120 GB free disk, and
   arm64 coverage is partial, so grade on x86_64 Linux. Pin a *small fixed* instance set:
   every distinct instance is another image.

Scoring reports the official binary `resolved` through `passed`, and a dense
fail-to-pass fraction through `score` — a sparse 0/1 reward tells an evolution study very
little about which direction a harness moved.

Requires `pip install swebench datasets docker` (not a Proteus dependency; imported lazily
so the rest of the framework runs without them).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from proteus.bench.task import BenchTask, workspace_diff
from proteus.core.goal import EvalResult

DEFAULT_DATASET = "SWE-bench/SWE-bench_Verified"
GRADE_TIMEOUT_S = 1800


def _load_instance(instance_id: str, dataset: str, split: str) -> dict[str, Any]:
    from datasets import load_dataset  # noqa: PLC0415
    for row in load_dataset(dataset, split=split):
        if row["instance_id"] == instance_id:
            return dict(row)
    raise KeyError(f"{instance_id} not in {dataset}:{split}")


def _setup(ws: Path, inst: dict[str, Any]) -> None:
    """Clone the instance's repository into the task workspace at `base_commit`."""
    url = f"https://github.com/{inst['repo']}.git"
    if not (ws / ".git").exists():
        subprocess.run(["git", "clone", "--quiet", url, str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "checkout", "--quiet", inst["base_commit"]],
                   check=True)


def _grade(ws: Path, inst: dict[str, Any], episode_tag: str) -> EvalResult:
    name = f"swebench:{inst['instance_id']}"
    diff = workspace_diff(ws, inst["base_commit"])
    if not diff.strip():
        return EvalResult(name=name, score=0.0, passed=False, detail="empty patch")

    try:
        import docker  # noqa: PLC0415
        from swebench.harness.run_evaluation import run_instance  # noqa: PLC0415
        # the flat `swebench.harness.test_spec` import is broken across versions
        from swebench.harness.test_spec.test_spec import make_test_spec  # noqa: PLC0415
    except ImportError as exc:
        return EvalResult(name=name, score=0.0, passed=False,
                          detail=f"grading deps missing ({exc}); "
                                 "pip install swebench datasets docker")

    spec = make_test_spec(inst)
    pred = {"instance_id": inst["instance_id"], "model_name_or_path": "proteus",
            "model_patch": diff}
    result = run_instance(spec, pred, docker.from_env(),
                          run_id=f"proteus-{episode_tag}",   # never reuse: see module doc
                          timeout=GRADE_TIMEOUT_S)
    if not result:
        return EvalResult(name=name, score=0.0, passed=False,
                          detail="patch did not apply, or the harness errored")

    report = result[1][inst["instance_id"]]
    resolved = bool(report.get("resolved"))
    f2p = (report.get("tests_status", {}) or {}).get(
        "FAIL_TO_PASS", {"success": [], "failure": []})
    ok, bad = len(f2p.get("success", [])), len(f2p.get("failure", []))
    dense = ok / (ok + bad) if (ok + bad) else 0.0
    return EvalResult(name=name, score=1.0 if resolved else dense, passed=resolved,
                      detail=f"resolved={resolved}; fail_to_pass {ok}/{ok + bad}")


def swe_task(instance_id: str, *, dataset: str = DEFAULT_DATASET,
             split: str = "test", episode_tag: str = "ep") -> BenchTask:
    """One SWE-bench instance as a `BenchTask`.

    `episode_tag` must differ per episode — pass e.g. `f"ep{episode}"` — or the official
    harness returns the previous episode's cached verdict.
    """
    inst = _load_instance(instance_id, dataset, split)
    return BenchTask(
        id=f"swebench:{instance_id}",
        goal_text=inst["problem_statement"],
        setup=lambda ws: _setup(ws, inst),
        grade=lambda ws: _grade(ws, inst, episode_tag),
        base_commit=inst["base_commit"],
    )
