# Proteus

**A harness-agnostic driver for measuring agent self-evolution.**

Plug in *any* agent harness × *any* model, let it rewrite its own harness over many
context-fresh episodes, and measure **how the harness changes** — under a goal, many goals,
or no goal at all.

> Named for the sea-god who changes shape at will: Proteus watches a harness reshape
> itself, and gives you the ruler to measure the change.

---

## Why Proteus is different

Agent self-improvement is moving from the weights to the **harness** — the prompts, memory,
skills, tools, and control loop the model runs on. Recent systems evolve a harness to raise
a benchmark score. Proteus asks a different, complementary question: **what does a
self-evolving harness actually *do*, and does an initial condition leave a permanent mark?**

Three things set it apart from every existing harness-evolution system:

1. **Harness-agnostic.** Others evolve harnesses built from their *own* primitives. Proteus
   evolves *yours*: implement one small `HarnessAdapter` and your agent — Aki (default), a
   bare ReAct loop, or your own — plugs into the same driver, sandbox, and measurement.
2. **Goal *and* no-goal, with visible or hidden evaluators.** Others hard-code a single
   regime: one benchmark verifier, agent blind to the score, goal mandatory. Proteus spans
   the space — `no-goal | one goal | many goals`, and evaluators that the agent either
   **sees** (in the observe phase) or **never sees**. No-goal, unpressured evolution is a
   first-class mode.
3. **A measurement instrument, not just a score.** Others report task pass-rates. Proteus
   ships the ruler for the harness itself: **structural distance** between harness states
   (per surface, path-length), a **crystallization / swap** test (remove the disposition,
   read the harness back), and **behavioural distance** with a permutation test (the
   action-preference statistic). Every condition is read with the same ruler.

## Install

```bash
pip install -e .            # core is dependency-free
pip install -e '.[llm]'     # add a real-LLM policy (openai)
```

## 60-second demo (no API key, no Docker)

The bundled `minimal` harness runs fully offline, so you can see the whole pipeline before
wiring up a real agent:

```bash
proteus run --harness minimal \
    --arm neutral --arm review:notes --arm review:tools \
    --seeds 4 --episodes 8 --out runs/demo
proteus measure --harness minimal --out runs/demo
```

```
arm              seeds       notes       tools   (mean units built)
neutral              4         4.0         2.8
review_notes         4        13.2         0.0
review_tools         4         3.5         8.0

behavioural R (between/within arms, last episode): 2.433  p=0.0170
```

An installed action preference measurably shifts what the harness grows — and the same
`measure` reads a no-goal run and a goal run identically.

## How it works

Every seed runs `N` context-fresh **episodes**; only files cross the episode boundary. One
episode is four phases:

```
observe  →  propose  →  act  →  reflect
```

- **observe** — take stock; if you configured a *visible* evaluator, its score on the last
  episode is shown here.
- **propose** — list ways to improve your own harness.
- **act** — carry one out by editing the harness (the goal, if any, is announced here).
- **reflect** — decide what to keep.

The **driver** owns everything that is not the harness (prompts, goal text, evaluator
routing, snapshotting, measurement). The **adapter** owns everything that is (how the four
phases actually execute). That split is what makes Proteus harness-agnostic.

### The core objects

| Concept | What it is |
|---|---|
| `HarnessAdapter` | the contract a harness implements: its surfaces, how to run an episode, how to read the action trace, how to install/remove a disposition |
| `Surface` | one editable, persistent region (memory / skills / tools / code / …), declared as data so the measurement layer needs no hard-coded names |
| `Disposition` | the action-preference perturbation — a **single, removable** change at t=0 (prompt suffix, config value, or code patch) |
| `GoalConfig` | goal / no-goal / multi-goal, each evaluator `HIDDEN` or `OBSERVE`-visible |
| `Sandbox` | where an episode runs; `LocalSandbox` (trusted) or `DockerSandbox` (OS-level isolation, tunable network) |

### Action preference

An action preference is installed as a `Disposition` and is guaranteed **removable**, so the
crystallization test can take it away and read what the harness built on its own:

```python
from proteus.core import review, record, NEUTRAL
review("memory")     # each phase: review your memory, act or not
record("tools")      # keep your tools current as you work
NEUTRAL              # the control, F0 — no perturbation
```

### Goals and evaluators

```python
from proteus.core import GoalConfig, Goal, Visibility

GoalConfig.no_goal()                                   # unpressured evolution
GoalConfig.single(Goal("solve", text=..., evaluator=my_eval,
                       visibility=Visibility.OBSERVE))  # agent sees its score
GoalConfig.multi([...])                                 # several objectives at once
```

An evaluator is any callable `(trace, ctx) -> EvalResult`; bring a benchmark verifier, an
LLM judge, or an intrinsic measure.

### Sandbox

```python
from proteus.sandbox import SandboxConfig, DockerSandbox
DockerSandbox(SandboxConfig(network="none"))    # no egress
DockerSandbox(SandboxConfig(network="host",     # needs an LLM endpoint
                            env_passthrough=("OPENAI_API_KEY",),
                            mem_limit="4g"))
```

A self-editing agent writes and runs its own code, so an application-level file sandbox
cannot contain it — Proteus runs real harnesses in a container whose filesystem holds the
harness and nothing else.

## Plug in your own harness

Implement `HarnessAdapter` (see `proteus/adapters/minimal.py` for a ~120-line reference and
`docs/ADAPTERS.md` for the guide). Declare your surfaces, run one episode, emit a normalized
action trace, and install a removable disposition. That's it — the driver, sandbox, and the
entire measurement suite work unchanged.

## Measurement

```python
from proteus.measure import distance, stream, crystallize
```

- `distance` — structural distance per surface (added / dropped / revised), path length.
- `stream` — behavioural distance (frequency / order / procedure) and the between/within
  permutation test `R`.
- `crystallize` — mount an evolved state under a neutral disposition and test whether it
  reads back as its own endpoint (two-stage fidelity + arm-shift).

## Status

`v0.1.0`. The `minimal` harness and the full measurement suite are working and offline. The
`aki` adapter is the reference research integration (set `AKI_HARNESS_SRC`). Proteus is the
open framework behind our paper on action preference as an initial condition for
self-improving agents; it is designed to reproduce every experiment in that work.

## License

MIT.
