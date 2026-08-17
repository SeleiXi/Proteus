"""The self-evolution driver: run a harness for N context-fresh episodes and record its
trajectory.

One episode is four phases — **observe → propose → act → reflect** — context-fresh each
time; only files cross the episode boundary. The driver owns everything that is *not* the
harness: it builds each phase's prompt (folding in the goal text and any evaluator feedback
the agent is allowed to see), asks the adapter to run the episode, snapshots the working
tree, runs the evaluators, and applies the outer-loop selection if one is configured. The
adapter owns everything that *is* the harness (how the four phases actually execute).

This separation is what makes Proteus harness-agnostic and condition-complete at once: the
same driver runs Aki or a bare ReAct loop, under no-goal or multi-goal, with evaluators
hidden or visible, and the measurement layer reads all of it with one ruler.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from proteus.core.adapter import EpisodeSpec, HarnessAdapter
from proteus.core.disposition import Disposition
from proteus.core.goal import EvalResult, GoalConfig, GoalContext, Visibility
from proteus.core import snapshot

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
    # the disposition contributes its (per-phase) text
    for ph in PHASES:
        suffix = cfg.disposition.phase_text(ph)
        if suffix:
            prompts[ph] = f"{prompts[ph]}\n\n{suffix}"
    return prompts


def run(cfg: RunConfig) -> RunResult:
    """Run one seed's full trajectory, harness retained under `cfg.root`."""
    harness = cfg.root / "harness"
    cfg.adapter.seed(harness)
    cfg.adapter.install_disposition(harness, cfg.disposition)
    snapshot.init(harness)

    eval_history: list[dict] = []
    prior_feedback = ""
    error = ""
    done = 0
    for ep in range(1, cfg.episodes + 1):
        spec = EpisodeSpec(
            root=cfg.root, episode=ep, model=cfg.model,
            phase_prompts=_phase_prompts(cfg, prior_feedback),
            max_turns=cfg.max_turns,
        )
        try:
            res = cfg.adapter.run_episode(spec)
        except Exception as exc:  # noqa: BLE001 - a failed episode is a record, not a crash
            error = f"{type(exc).__name__}: {exc}"
            break
        if not res.ok:
            error = res.error
            break
        snapshot.commit(harness, f"episode {ep}: {cfg.name}")
        done = ep

        # evaluate; route results by visibility
        trace = cfg.adapter.read_trace(cfg.root, ep)
        results = cfg.goal.evaluate(trace, GoalContext(str(harness), ep))
        by_name = {r.name: r for r in results}
        eval_history.append({"episode": ep, "results": [r.__dict__ for r in results]})
        prior_feedback = cfg.goal.observe_feedback(by_name)  # OBSERVE-visible only

        # outer-loop selection on HIDDEN scores (accept/reject the episode's edits)
        if cfg.goal.selection == "accept_reject" and results:
            worst = min(r.score for r in results)
            # a real policy would compare to best-so-far; the hook is here for goal runs.
            _ = worst

    (cfg.root / "eval_history.json").write_text(json.dumps(eval_history, indent=1))
    return RunResult(name=cfg.name, episodes_complete=done, root=str(cfg.root),
                     error=error, eval_history=eval_history)
