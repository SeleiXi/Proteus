# Recipes

Complete, copy-paste sequences from a stock harness to measured self-evolution. Execution
status is stated per recipe.

## Pi (pi-coding-agent) — minimal harness, full pipeline

[Pi](https://github.com/badlogic/pi-mono) is Mario Zechner's deliberately minimal coding
harness: four built-in tools, native `AGENTS.md`, native skills. Nothing in it knows about
Proteus — which is the point.

Status: steps 1–2 executed (image built; `proteus check --harness pi` passes 8/8 static +
provisioning; the container reaches the DeepSeek endpoint and writes session JSONL).
Steps 3–6 are wired but not yet executed live.

```bash
# 1. prepared environment (Node 24 + pi, pinned)
docker build -q -t proteus-env-pi:0.84.2 environments/pi/

# 2. contract check (free: static + provisioning; --episode adds one live episode)
proteus check --harness pi
proteus check --harness pi --episode          # needs DEEPSEEK_API_KEY

# 3. self-evolution: two arms, no goal
export DEEPSEEK_API_KEY=...
proteus run --harness pi \
    --arm neutral --arm review:notes \
    --seeds 2 --episodes 3 --out runs/pi-demo

# 4. watch it live (from a second terminal, any time after step 3 starts)
proteus watch --out runs/pi-demo              # http://localhost:8300/report.html

# 5. measure with the same ruler every harness gets
proteus measure --harness pi --out runs/pi-demo --travel

# 6. keep the evolution history as a git repo; push it if you want
proteus repo export runs/pi-demo/runs/run-<id> pi-evolution
git -C pi-evolution log --oneline             # one commit per episode
```

What the adapter does (~150 lines, `proteus/adapters/pi.py`): seeds `AGENTS.md` +
`notes/ tools/ skills/`, installs the disposition as a removable marked block in
`AGENTS.md` (pi loads it natively), runs one `pi -p` session per phase in the container
(`--session-dir` pointed at a mounted state dir, `--skill /workspace/skills`), and parses
pi's session JSONL (`message` events, `toolCall` blocks) into the normalized trace.

## DeepSeek Harness (dsh) — plugin-architecture harness

Same shape, different harness (see `proteus/adapters/dsh.py`; environment in
`environments/deepseek-harness/`). Executed 2026-08-17: 2 arms x 2 episodes, all complete;
both agents edited their own `AGENTS.md`; the installed disposition block survived on the
review arm and the fingerprint separated the arms.

```bash
docker build -q -t proteus-env-dsh:0.1.0-rc.7 environments/deepseek-harness/
proteus run --harness dsh --arm neutral --arm review:notes \
    --seeds 1 --episodes 2 --out runs/dsh-demo
proteus measure --harness dsh --out runs/dsh-demo
```

## Your harness

```bash
proteus env scaffold --from <git-url-or-local-path> --name yours
proteus env build yours
# write the adapter (docs/ADAPTERS.md), then:
proteus check --harness mypkg.yours_adapter:YoursHarness --episode
```

## With-goal ablation — benchmarks as goals

Proteus's goal axis becomes an experiment when the goal is a *task* and the evaluator is
its grader. `proteus.bench` supplies both from one object:

```python
from proteus.bench import as_goal
from proteus.bench.local import local_task
from proteus.core import Visibility
from proteus.core.episode import RunConfig, run

task = local_task("local:interval-merge")        # offline; no Docker, no dataset
cfg = RunConfig(name="goal-observe", adapter=..., disposition=...,
                goal=as_goal(task, visibility=Visibility.OBSERVE),
                root=..., model=..., episodes=30, task=task)
run(cfg)
```

The task is seeded into `harness/task/` before episode 1 and graded after every episode, so
the ablation is a matter of swapping one field:

| condition | `goal=` |
|---|---|
| no goal (this paper's primary regime) | `GoalConfig.no_goal()` |
| goal, score hidden from the agent | `as_goal(task)` |
| goal, score shown in the next observe phase | `as_goal(task, visibility=Visibility.OBSERVE)` |
| goal + outer-loop rejection of regressions | `as_goal(task, selection="accept_reject")` |

**Two workspaces, do not confuse them.** `harness/` is what evolves and what the rulers
measure; `harness/task/` is what the agent was asked to work on. Task files land inside the
harness workspace on purpose — every adapter already gives the agent file access there, so
a benchmark needs no adapter change — but that means the measurement layer counts them as
structure unless you exclude `TASK_SUBDIR`.

### SWE-bench

`proteus.bench.swe.swe_task("django__django-11133")` grades through the official harness
(`make_test_spec` + `run_instance`), reporting the binary `resolved` as `passed` and the
fail-to-pass fraction as `score` (a sparse 0/1 reward says little about *direction*, which
is what an evolution study reads).

Written against the verified upstream API; **not yet executed** — it needs an x86_64 Linux
box with ~120 GB free disk (per-instance images), `pip install swebench datasets docker`,
and network for the first pull. Three constraints are load-bearing and documented in the
module: the only bridge to the grader is `git diff base_commit` (so the task workspace must
be that repo at that commit — `setup` clones it), the upstream cache keys on
`(run_id, instance_id)` and ignores the patch (so the run id embeds the episode), and every
distinct instance is another image (so pin a small fixed set).

## Post-run safety audit

Safety auditing is a second, read-only pass over a completed trajectory. It never changes
the episode prompts, goal evaluator, accept/reject selection, or later harness state.

```bash
# 1. Produce a complete offline trajectory.
proteus run --harness minimal --arm neutral \
    --seeds 1 --episodes 2 --out runs/audit-demo

# 2. Audit immutable episode snapshots and normalized traces.
proteus audit --harness minimal --out runs/audit-demo \
    --audit-id instrument-integrity-v1

# 3. Generate and serve the report after the audit is published.
proteus report --out runs/audit-demo
proteus watch --out runs/audit-demo
```

The default suite checks the measurement substrate: snapshot materialization, trace
availability, canonical phase names, and whether a reflect-phase self-assessment signal was
exposed. It does not claim that the harness is safe. The report renders audit counts in a
separate table; audit results never become task scores or evolution feedback.

Use a custom adapter-specific suite through the same extension style as harness adapters:

```bash
proteus audit --harness mypkg.adapter:MyHarness --out runs/my-study \
    --suite mypkg.safety:SUITE --audit-id native-cases-v1
```

An artifact suite receives a disposable materialization, the adapter's declared `Surface`s,
and its normalized `ActionEvent` trace. If a case needs to execute historical or
agent-authored code, the suite must run a separate disposable copy inside an OS containment
boundary; it must never mount the source trajectory read-write.

An Aki-specific pack can map native Memory, Skills, and Authored Tools evidence into the
portable taxonomy without hard-coding those surface names into Proteus core. Composition is
recorded as a cross-surface scope, and missing native permission/tool-result evidence stays
`not_evaluated`.
