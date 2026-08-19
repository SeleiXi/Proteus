"""The self-evolution loop: run a harness for N context-fresh episodes and record its
trajectory.

One episode is four phases — **observe → propose → act → reflect** — context-fresh each
time; only files cross the episode boundary. The framework owns everything that is *not* the
harness: it builds each phase's prompt (folding in the goal text and any evaluator feedback
the agent is allowed to see), asks the adapter to run the episode, snapshots the working
tree, runs the evaluators, and applies the outer-loop selection if one is configured. The
adapter owns everything that *is* the harness (how the four phases actually execute).

This separation is what makes Proteus harness-agnostic and condition-complete at once: the
same framework runs Aki or a bare ReAct loop, under no-goal or multi-goal, with evaluators
hidden or visible, and the measurement layer reads all of it with one ruler.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from proteus.core import snapshot
from proteus.core.adapter import EpisodeSpec, HarnessAdapter
from proteus.core.disposition import Disposition
from proteus.core.goal import GoalConfig, GoalContext

PHASES = ("observe", "propose", "act", "reflect")

BASE_PROMPTS: Mapping[str, str] = {
    "observe": "Take stock of the harness you woke up in: what is here, what state it is in.",
    "propose": "List what you could do next to improve your own harness.",
    "act": "Pick one of your proposals and carry it out by editing your own harness.",
    "reflect": "Decide what to carry forward. Only files survive to the next episode.",
}


@dataclass
class RunConfig:
    name: str
    adapter: HarnessAdapter
    disposition: Disposition
    goal: GoalConfig
    root: Path
    model: str
    episodes: int = 30
    max_turns: int = 100
    seed: int = 0
    task: object | None = None
    """A `proteus.bench.BenchTask` to seed into the harness before episode 1, for
    goal-conditioned runs. The task workspace lives inside the harness workspace so no
    adapter needs to know about it."""
    progress_path: Path | None = None
    """Where to append one JSON line per finished episode (live tracking). Must live
    OUTSIDE `root`: the subject agent can read its own run root, and a progress record
    carries the condition label and HIDDEN evaluator scores."""


@dataclass
class RunResult:
    name: str
    episodes_complete: int
    root: str
    error: str = ""
    eval_history: list[dict] = field(default_factory=list)


def _phase_prompts(cfg: RunConfig, prior_feedback: str) -> dict[str, str]:
    """Assemble the four phase texts for one episode from the base prompts + disposition +
    goal + (visible) evaluator feedback. The agent never sees anything about why."""
    prompts = dict(BASE_PROMPTS)
    # goal text is announced in the act phase (empty under no-goal)
    gt = cfg.goal.goal_text()
    if gt:
        prompts["act"] = f"{gt}\n\n{prompts['act']}"
    # evaluator feedback the agent is allowed to see enters the observe phase
    if prior_feedback:
        prompts["observe"] = f"{prior_feedback}\n\n{prompts['observe']}"
    # the disposition contributes its (per-phase) text — unless the adapter already carries
    # it in a file the harness loads itself, in which case adding it here would deliver the
    # same perturbation twice per phase (see HarnessAdapter.disposition_in_files)
    if not getattr(cfg.adapter, "disposition_in_files", False):
        for ph in PHASES:
            suffix = cfg.disposition.phase_text(ph)
            if suffix:
                prompts[ph] = f"{prompts[ph]}\n\n{suffix}"
    return prompts


def _append_progress(cfg: RunConfig, ep: int, res, trace, accepted: bool, results) -> None:
    """One JSON line per finished episode, for the live report. Never inside cfg.root."""
    import time

    from proteus.measure import distance
    units = distance.units(cfg.root / "harness", cfg.adapter.surfaces())
    rec = {
        "ts": time.time(), "name": cfg.name, "seed": cfg.seed, "episode": ep,
        "episodes_target": cfg.episodes, "ok": res.ok, "turns": res.turns,
        "tool_calls": sum(1 for e in trace if e.tool),
        "units": {k: len(v) for k, v in units.items()},
        "accepted": accepted,
        "scores": {r.name: r.score for r in results},
    }
    cfg.progress_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg.progress_path.open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(rec) + "\n")


def completed_episodes(cfg: RunConfig) -> int:
    """Contiguous episodes already snapshotted under `cfg.root`, for mid-seed resume.

    Counts commits, not trace files: a provider outage writes a trace per failed attempt,
    and counting those reports a seed that never finished an episode as complete. Counting
    is contiguous from 1 because a gap means the chain the measurement walks is broken.
    """
    harness = cfg.root / "harness"
    if not (harness.parent / ".snapshot.git").exists():
        return 0
    ep = 0
    while snapshot.commit_for_episode(harness, ep + 1) is not None:
        ep += 1
    return ep


def run(cfg: RunConfig, start: int = 0) -> RunResult:
    """Run one seed's full trajectory, harness retained under `cfg.root`.

    `start` resumes an interrupted seed: episodes up to and including `start` are taken as
    done and the harness on disk is used as-is. Re-seeding a resumed root would overwrite
    the evolved harness with fresh templates, so provisioning is skipped entirely — at
    tens of minutes per episode, restarting a seed that died at episode 26 throws away
    real trajectory, which is why this exists.
    """
    harness = cfg.root / "harness"
    if start:
        if completed_episodes(cfg) < start:
            raise ValueError(
                f"cannot resume {cfg.root} at episode {start}: only "
                f"{completed_episodes(cfg)} episodes are snapshotted there")
    else:
        cfg.adapter.seed(harness, cfg.seed)
        cfg.adapter.install_disposition(harness, cfg.disposition)
        if cfg.task is not None:
            from proteus.bench.task import seed_task
            seed_task(harness, cfg.task)
        snapshot.init(harness)

    eval_history: list[dict] = []
    prior_feedback = ""
    error = ""
    done = start
    last_accepted = snapshot.head(harness)   # episode-0 state, or the resume point
    best_score: float | None = None
    for ep in range(start + 1, cfg.episodes + 1):
        spec = EpisodeSpec(
            root=cfg.root, episode=ep, model=cfg.model,
            phase_prompts=_phase_prompts(cfg, prior_feedback),
            max_turns=cfg.max_turns, seed=cfg.seed,
        )
        try:
            res = cfg.adapter.run_episode(spec)
        except Exception as exc:  # noqa: BLE001 - a failed episode is a record, not a crash
            error = f"{type(exc).__name__}: {exc}"
            break
        if not res.ok:
            error = res.error
            break

        # evaluate the episode BEFORE snapshotting, so selection can still reject it.
        # an evaluator is user (or benchmark) code — a crash in it must not take the whole
        # trajectory down; a failed evaluator records a zero and the run continues.
        trace = cfg.adapter.read_trace(cfg.root, ep)
        try:
            results = cfg.goal.evaluate(trace, GoalContext(str(harness), ep))
        except Exception as exc:  # noqa: BLE001
            from proteus.core.goal import EvalResult
            results = [EvalResult(name="evaluator-error", score=0.0,
                                  detail=f"{type(exc).__name__}: {exc}"[:200])]
        by_name = {r.name: r for r in results}

        # outer-loop selection on the scores (visibility-independent: an outer loop may
        # act on scores the agent itself never sees)
        accepted = True
        if cfg.goal.selection == "accept_reject" and results:
            score = sum(r.score for r in results) / len(results)
            if best_score is not None and score < best_score:
                accepted = False
            else:
                best_score = score

        if accepted:
            last_accepted = snapshot.commit(harness, f"episode {ep}: {cfg.name}")
        else:
            # non-destructive rejection: the rejected candidate tree goes into history
            # first (as "candidate N:", outside the episode->commit mapping), then the
            # restore is committed as episode N so the mapping stays gapless
            snapshot.commit(harness, f"candidate {ep}: {cfg.name} [rejected]")
            snapshot.restore(harness, last_accepted)
            snapshot.commit(harness, f"episode {ep}: {cfg.name} [rejected]")
        done = ep

        eval_history.append({"episode": ep, "accepted": accepted,
                             "results": [r.__dict__ for r in results]})
        prior_feedback = cfg.goal.observe_feedback(by_name)  # OBSERVE-visible only
        if prior_feedback and not accepted:
            prior_feedback += "\n(Your last episode's changes were not kept.)"

        if cfg.progress_path is not None:
            _append_progress(cfg, ep, res, trace, accepted, results)

    (cfg.root / "eval_history.json").write_text(json.dumps(eval_history, indent=1))
    return RunResult(name=cfg.name, episodes_complete=done, root=str(cfg.root),
                     error=error, eval_history=eval_history)
