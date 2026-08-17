"""Action preference as an installable, removable disposition.

This is the framework's answer to "is there a generic interface to inject an action
preference?" — yes, and it is deliberately harness-agnostic.

An action preference is a **single perturbation at t=0** to the disposition region `F` of
the harness, leaving the working region `W` untouched. Proteus treats `F` uniformly no
matter how a given harness expresses it: as text prepended to a phase prompt, as a
key=value substituted into a harness config, or as a code patch. What every form shares,
and what the framework requires, is:

  * it is a **single planted difference** (so any later divergence in `W` is attributable);
  * it is **removable** (so crystallization can read the harness back without it);
  * a harness never learns *why* the preference is set — the disposition may say what to
    do, never that it is an experimental manipulation.

The adapter decides how to apply a `Disposition` to its own harness
(`HarnessAdapter.install_disposition`); this module defines the portable object and the
common ways to build one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Disposition:
    """A removable action-preference perturbation.

    Exactly one of the application modes is used by a given adapter:

    - `prompt_suffix`: text appended to each phase's instruction (the lightest form;
      works for any harness that has phase prompts). `per_phase` overrides it per phase.
    - `config`: key -> value pairs the adapter substitutes into a harness config/module
      (e.g. `SURFACE="memory"`), for harnesses whose disposition lives in code/config.
    - `patch`: a unified diff applied on top of the seeded harness (the most general form;
      removal is a revert), for harnesses whose disposition is woven through the loop.

    `label` is the opaque condition id used on disk and in records — never a name that
    spells the manipulation.
    """

    label: str
    prompt_suffix: str = ""
    per_phase: Mapping[str, str] = field(default_factory=dict)
    config: Mapping[str, str] = field(default_factory=dict)
    patch: str = ""

    @property
    def is_empty(self) -> bool:
        """The neutral control: no perturbation. `F0` in the crystallization swap."""
        return not (self.prompt_suffix or self.per_phase or self.config or self.patch)

    def phase_text(self, phase: str) -> str:
        """The text this disposition contributes to a given phase's prompt (may be empty)."""
        return self.per_phase.get(phase, self.prompt_suffix)


# ----------------------------------------------------------------------------------------
# Builders for the common research dispositions. These are content only — they say what to
# do, never why. An adapter maps them onto its own harness.
# ----------------------------------------------------------------------------------------

NEUTRAL = Disposition(label="neutral")


def review(surface: str, label: str | None = None) -> Disposition:
    """A per-phase *review* demand over one surface, with a free 'or nothing' exit.

    "Before your closing reply, go over your {surface} and decide whether it needs
    anything — a new entry, a revision, or nothing. Act on your decision."
    """
    text = (
        f"Before your closing reply, go over your {surface} and decide whether it needs "
        f"anything — a new entry, a revision, or nothing. Act on your decision."
    )
    return Disposition(label=label or f"review_{surface}", prompt_suffix=text,
                       config={"SURFACE": surface})


def record(surface: str, label: str | None = None) -> Disposition:
    """A stronger *record* nudge to act on one surface each step."""
    text = (
        f"As you work, keep your {surface} current: when something belongs in {surface}, "
        f"write it now rather than deferring."
    )
    return Disposition(label=label or f"record_{surface}", prompt_suffix=text,
                       config={"SURFACE": surface})
