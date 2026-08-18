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
