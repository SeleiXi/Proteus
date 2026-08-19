# Onboarding a harness

The input for onboarding is a **repository** — a git URL or a local path to the harness
you want to evolve. Onboarding produces two artifacts:

1. a **prepared environment** — a pinned Docker image carrying the harness itself
   (the evolving workspace is never in the image; it is always a mount);
2. an **adapter** — one class implementing `proteus.core.HarnessAdapter`, the only code
   you write.

Once both exist, the framework, sandbox, and the whole measurement suite work on your
harness unchanged, and the CLI loads your adapter with no registration.

```bash
# 1. point Proteus at the harness repo (git URL or local path)
proteus env scaffold --from https://github.com/org/their-harness --name theirs --ref v1.2.0

# 2. build the pinned environment image (uses the repo's own Dockerfile, or your wrapper)
proteus env build theirs
#    -> proteus-env-theirs:<shortsha>, resolved sha recorded in environments/theirs/environment.toml

# 3. write the adapter (the seven methods below), then verify it holds the contract
proteus check --harness mypkg.theirs_adapter:TheirsHarness            # free, static
proteus check --harness mypkg.theirs_adapter:TheirsHarness --episode  # + one live episode

# 4. run and measure
proteus run --harness mypkg.theirs_adapter:TheirsHarness \
    --arm neutral --arm review:notes --seeds 4 --episodes 10 --out runs/theirs
proteus measure --harness mypkg.theirs_adapter:TheirsHarness --out runs/theirs --travel
```

If the repo ships no Dockerfile, `proteus env scaffold --local-dockerfile` writes a wrapper
stub under `environments/<name>/` that is built with the repo checkout as its context —
put the runtime the harness needs there (see `environments/deepseek-harness/Dockerfile`:
Node 24 for dsh, telemetry disabled, version pinned).

## The contract

```python
class TheirsHarness:
    name = "theirs"
    disposition_in_files = False   # True if install_disposition writes a file the
                                   # harness loads itself (see step 3)

    def surfaces(self) -> Sequence[Surface]: ...
    def required_edit_tools(self) -> frozenset[str]: ...
    def seed(self, harness_root: Path, rng_seed: int = 0) -> None: ...
    def install_disposition(self, harness_root: Path, disposition: Disposition) -> None: ...
    def run_episode(self, spec: EpisodeSpec) -> EpisodeResult: ...
    def read_trace(self, root: Path, episode: int) -> Sequence[ActionEvent]: ...
    def disposition_fingerprint(self, harness_root: Path) -> str: ...
```

Two reference implementations cover the two integration shapes:

- **In-process** — you control the harness code (a Python library, or callable
  in-process): start from `proteus/adapters/minimal.py` (~120 lines).
- **External CLI** — the harness is someone else's program and you should not modify it:
  start from `proteus/adapters/dsh.py`. Its episode launches the stock CLI inside the
  prepared image per phase, the disposition installs as a removable marked block in a file
  the harness already reads (`AGENTS.md`), and the trace is parsed from the harness's own
  session logs.

### 1. Declare surfaces
A `Surface` is one editable, persistent region the agent can grow. Declaring them as data
is what lets Proteus measure any harness:

```python
Surface("memory", "memory", unit="file",      write_tools=frozenset({"memory_write"}))
Surface("skills", "skills", unit="directory", write_tools=frozenset({"skill_write"}))
Surface("tools",  "tools",  unit="file",      write_tools=frozenset({"tool_write"}), is_code=True)
```

`unit` is how the measurement layer counts (a file, a directory, or a top-level def in a
code file). `free_named=True` means the agent picks unit names. If the stock harness has no
such regions, the adapter may establish them by convention in `seed` — the dsh adapter
seeds `notes/` + `tools/` and names them in the instructions file.

### 2. Seed
`seed(harness_root, rng_seed)` writes the episode-0 state: the workspace files the harness
starts from. Proteus snapshots this as commit 0. Episodes must tolerate waking up without
empty directories (git snapshots do not track them).

### 3. Install a removable disposition
`install_disposition` applies the action-preference perturbation; reinstalling `NEUTRAL`
must remove it without residue (`proteus check` verifies both directions via the
fingerprint). Pick the carrier that fits:
- **prompt** — append `disposition.phase_text(phase)` / `prompt_suffix` to phase prompts
  or to an instructions file the harness reads (simplest; dsh uses a marked block);
- **config** — substitute `disposition.config` into a config file;
- **patch** — apply `disposition.patch` as a diff (most general; removal is a revert).

By default Proteus also appends `disposition.phase_text(phase)` to every phase prompt, which
is the whole perturbation for a harness with no instructions file. If your carrier is a file
the harness loads on its own, set `disposition_in_files = True`: otherwise the same text
arrives twice per phase — about double the intended dose, through two channels of different
salience — and the prompt copy sits outside `F`, so it is neither removable nor covered by
`disposition_fingerprint`, which is what the attribution argument rests on.

### 4. Run one episode, emit the trace
`run_episode(spec)` executes the four phases (`spec.phase_prompts` carries goal text and
visible evaluator feedback already merged). `read_trace` returns normalized `ActionEvent`s
— the only behaviour channel Proteus reads; never self-report. An external harness's own
logs are the source of truth: parse them, do not instrument the harness.

### 5. Fingerprint
`disposition_fingerprint` hashes the currently-installed disposition carrier, so drift of
F over episodes is detectable (a self-editing agent may rewrite its own disposition).

## Isolation

If the harness lets the agent run its own code (most do), episodes must run under
`DockerSandbox` — an application-level file sandbox cannot contain a process that writes
and executes code. Use per-call mounts for your container layout (see the dsh adapter);
declare network policy in the environment manifest, `none` unless the harness itself must
reach an API.

## Checklist

- [ ] environment: image pinned (repo sha recorded in the manifest), state via mounts only
- [ ] surfaces declared as data (or established by convention in `seed`)
- [ ] disposition install is removable — `proteus check` passes
- [ ] trace parsed from the harness's own logs into `ActionEvent`s
- [ ] real (code-running) harness under `DockerSandbox`
- [ ] `proteus check --harness <module>:<Class> --episode` passes

## Auditing a finished sweep

Two things invalidate a trajectory without touching its score, so they are read from the
run rather than assumed away.

```bash
proteus reliability --out runs/mine --harness aki   # run this before `measure`
proteus audit       --out runs/mine --harness aki
```

`reliability` asks whether each arm reproduces itself, against a composition-matched null:
streams resampled from the arm's own pooled tool marginal, same length and same tool mix,
no shared procedure. `R` divides between-arm distance by within-arm distance, so an arm
whose runs do not resemble each other has a denominator made of noise and its R means
nothing whichever way it comes out. A ratio near 0 is highly reproducible; the
pre-registered threshold is 0.5.

`audit` reports two kinds of evidence, quoted rather than scored:

- **escape** — authored code that can leave the harness (`subprocess`, `eval`, a socket, a
  URL fetch), and any path fragment naming the study's own directories. Files identical to
  the episode-0 snapshot are skipped, so a harness that ships a CLI importing `subprocess`
  does not flag every seed.
- **awareness** — the subject describing its own situation as an experiment.

Both are deliberately over-inclusive: a tool may import `subprocess` and never escape.
The output is what to read, not what to exclude. Recall is limited in the other direction
too — in our own fleet one seed quarantined as aware matches no pattern and was caught by
a person reading its notes, so a clean audit means "no evidence found", not "clean".

Containment itself is the sandbox's job (`proteus.sandbox`): a harness that authors its own
tools can always defeat a pattern list, which is why the audit runs after the fact and the
isolation runs during.

## Resuming an interrupted seed

Episodes cost minutes to tens of minutes, so a seed that dies at episode 26 is hours of
trajectory. `run(cfg, start=N)` continues from the episode after `N`, using the harness on
disk rather than re-seeding over it, and `proteus run --on-existing resume` does this for a
whole sweep: finished seeds are skipped, partial ones pick up at the episode after their
last snapshot. `completed_episodes()` counts contiguous snapshot commits, not trace files —
a provider outage writes a trace per failed attempt, and counting those reports a seed that
finished nothing as complete.

## Letting the harness edit its own code

Instructions and notes are not the interesting ceiling: self-evolution that cannot touch
the loop is memory with extra steps. The arrangement that gives an external harness its
own code, without modifying the harness project, is the one Aki uses natively —
**copy the code into the harness at seed time, boot every episode from the copy**:

1. `seed()` extracts the harness's own code from the prepared image into `harness/src/`.
   For **pi this is the real TypeScript source** (~1,100 `.ts` files, the pi-mono checkout
   the `environments/pi-src/` image was built from); the image's entrypoint syncs the
   agent's copy over the baked tree at boot, rebuilds with the project's own toolchain
   when the source hash changes (outputs cached on `/state`; an untouched copy boots via
   a pristine-hash fast path), and execs the built CLI. For **dsh it is currently the
   published package's bundled ESM** (`lib/` + `config/`, ~216KB, readable) — the
   from-source arrangement is the same design, pending an image built from the dsh
   monorepo, whose pnpm + native-module build is a heavier bake. Dependencies stay in
   the image in both cases: they are apparatus, like the interpreter.
2. Every phase runs with `harness/src/<piece>` **shadow-mounted over the install path**
   (piecewise — one big mount would shadow the nested `node_modules` out of existence),
   so the stock binary boots the seed's copy. The agent edits `src/` with its ordinary
   workspace tools; the next session runs whatever it left.
3. `src/` is a declared surface (`loop`, `is_code=True`), inside the snapshot repo — code
   edits are versioned per episode and measured with the same ruler as notes and tools.
4. Before each episode, `check_boot()` runs `--version` through the boot path — the
   **viability gate**. For pi that includes the rebuild, so a type error the agent wrote
   into its own source surfaces as a build failure with the log tail; either way a copy
   that cannot boot never runs an episode, no API spend is burned discovering it, and
   the snapshot chain still holds every prior state.

Verified live for both harnesses: editing the copy changes what boots (a marker written
into pi's `cli.ts` appears after the automatic rebuild; a version edited into dsh's
`package.json` appears in `--version`), and a planted error — a TS type error for pi, a
JS syntax error for dsh — is caught by the gate and cleared by restoring the file.
