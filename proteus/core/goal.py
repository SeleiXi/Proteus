"""Goals and evaluators: the axis Proteus spans that fixed harness-evolvers do not.

Two orthogonal axes (see the paper's no-goal argument):

  Axis A — goal presence:  none | one | many goals.
  Axis B — evaluator wiring:
      * count:       0 / 1 / N evaluators
      * visibility:  where each evaluator's score goes
          - HIDDEN:    the agent never sees it; it is used only by an outer loop
                       (accept/reject a harness version) or for offline analysis.
          - OBSERVE:   the score is shown to the agent at the start of the next episode's
                       observe phase — the agent can react to it (feedback-seeking,
                       optimization, and the reward-hacking / Goodhart failure modes).

The `HIDDEN` mode reproduces the regime every fixed harness-evolver hard-codes
(agent-blind, offline). `OBSERVE` and the no-goal setting (no evaluator at all) are what
this framework adds. The measurement layer is identical across all settings, so a no-goal
run and a goal run are read with the same ruler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping, Sequence

from proteus.core.adapter import ActionEvent


class Visibility(str, Enum):
    HIDDEN = "hidden"        # agent never sees the score (offline / outer-loop only)
    OBSERVE = "observe"      # score shown in the next episode's observe phase


@dataclass(frozen=True)
class EvalResult:
    name: str
    score: float
    passed: bool = False
    detail: str = ""


# An evaluator scores one episode's trace (and optionally the harness state dir) into a
# result. It is a plain callable so a user can supply anything — a benchmark verifier, an
# LLM judge, a rule check, an intrinsic-novelty measure.
Evaluator = Callable[[Sequence[ActionEvent], "GoalContext"], EvalResult]


@dataclass(frozen=True)
class Goal:
    """One objective. `text` is shown to the agent (in the act phase); `evaluator` scores
    progress toward it. A goal with no evaluator is a stated aim with no measured feedback;
    an evaluator with no goal text is a measured signal with no announced objective."""

    name: str
    text: str = ""
    evaluator: Evaluator | None = None
    visibility: Visibility = Visibility.HIDDEN


@dataclass(frozen=True)
class GoalContext:
    """Passed to evaluators so they can resolve the harness state if they need it."""

    harness_root: str
    episode: int


@dataclass(frozen=True)
class GoalConfig:
    """The full goal/evaluator condition for a run.

    Presets cover the paper's conditions:
      - `GoalConfig.no_goal()`            → N0: no goal, no evaluator, no feedback.
      - `GoalConfig.single(goal, hidden)` → G-blind: one goal, offline agent-blind score.
      - `GoalConfig.single(goal, observe)`→ G-see-1: one goal, score visible in observe.
      - `GoalConfig.multi([...])`         → G-see-N / multi-goal.
    `selection` is what an OUTER loop does with hidden scores; orthogonal to visibility.
    """

    goals: tuple[Goal, ...] = ()
    selection: str = "none"            # "none" | "accept_reject" | "rank"

    @staticmethod
    def no_goal() -> "GoalConfig":
        return GoalConfig(goals=())

    @staticmethod
    def single(goal: Goal, *, selection: str = "none") -> "GoalConfig":
        return GoalConfig(goals=(goal,), selection=selection)

    @staticmethod
    def multi(goals: Sequence[Goal], *, selection: str = "none") -> "GoalConfig":
        return GoalConfig(goals=tuple(goals), selection=selection)

    @property
    def is_no_goal(self) -> bool:
        return len(self.goals) == 0

    def goal_text(self) -> str:
        """Objective text to show the agent in the act phase (empty under no-goal)."""
        stated = [g.text for g in self.goals if g.text]
        if not stated:
            return ""
        if len(stated) == 1:
            return stated[0]
        return "You are pursuing several objectives at once:\n" + "\n".join(
            f"  {i+1}. {t}" for i, t in enumerate(stated))

    def evaluate(self, trace: Sequence[ActionEvent], ctx: GoalContext) -> list[EvalResult]:
        """Run every evaluator. Caller decides what to do with the results per visibility."""
        out: list[EvalResult] = []
        for g in self.goals:
            if g.evaluator is not None:
                out.append(g.evaluator(trace, ctx))
        return out

    def observe_feedback(self, results: Mapping[str, EvalResult]) -> str:
        """The text shown to the agent in the next observe phase, for OBSERVE-visible goals
        only. HIDDEN-visibility scores never appear here."""
        lines = []
        for g in self.goals:
            if g.visibility is Visibility.OBSERVE and g.name in results:
                r = results[g.name]
                lines.append(f"- {g.name}: score {r.score:.3f}"
                             + (f" — {r.detail}" if r.detail else ""))
        if not lines:
            return ""
        return ("Feedback on your last episode from the evaluators you can see:\n"
                + "\n".join(lines))
