# Writing a Harness Adapter

An adapter is how *your* harness plugs into Proteus. It is one class implementing
`proteus.core.HarnessAdapter`. Once it exists, the framework, sandbox, and the whole
measurement suite work on your harness unchanged. `proteus/adapters/minimal.py` is a
complete ~120-line reference; this is the guide.

## The contract

```python
class MyHarness:
    name = "my-harness"

    def surfaces(self) -> Sequence[Surface]: ...
    def required_edit_tools(self) -> frozenset[str]: ...
    def seed(self, harness_root: Path) -> None: ...
    def install_disposition(self, harness_root: Path, disposition: Disposition) -> None: ...
    def run_episode(self, spec: EpisodeSpec) -> EpisodeResult: ...
    def read_trace(self, root: Path, episode: int) -> Sequence[ActionEvent]: ...
    def disposition_fingerprint(self, harness_root: Path) -> str: ...
```

### 1. Declare your surfaces
A `Surface` is one editable, persistent region the agent can grow. Declaring them as data
is what lets Proteus measure any harness:

```python
Surface("memory", "memory", unit="file",      write_tools=frozenset({"memory_write"}))
Surface("skills", "skills", unit="directory", write_tools=frozenset({"skill_write"}))
Surface("tools",  "tools",  unit="file",      write_tools=frozenset({"tool_write"}), is_code=True)
```

`unit` is how the measurement layer counts (a file, a directory, or a top-level def in a
code file). `free_named=True` means the agent picks unit names (so divergence matches
renamed-but-similar units).

### 2. Seed a fresh harness
`seed(harness_root)` writes the episode-0 state into `harness_root` — a fresh copy of your
agent's files (its package, loop, empty surfaces). Proteus snapshots this as commit 0.

### 3. Install a (removable) disposition
`install_disposition` applies the action-preference perturbation. Pick the form that fits
your harness:
- **prompt** — append `disposition.phase_text(phase)` to each phase prompt (simplest);
- **config** — substitute `disposition.config` (e.g. `SURFACE="memory"`) into a config file;
- **patch** — apply `disposition.patch` as a diff (most general; removal is a revert).

It **must be removable** — crystallization reads the harness back with `NEUTRAL` installed.

### 4. Run one episode, emit a normalized trace
`run_episode(spec)` executes the four phases using `spec.phase_prompts` (the framework has
already folded in the goal text and any visible evaluator feedback). Write the harness files
the agent edits, and record each step as an `ActionEvent`. Return an `EpisodeResult`;
results flow through the trace, not stdout.

`read_trace(root, episode)` returns those `ActionEvent`s. This is the only behaviour channel
Proteus reads — never self-report.

### 5. Fingerprint the disposition
`disposition_fingerprint` hashes the currently-installed disposition so drift of `F` over
episodes is detectable (a self-editing agent may rewrite its own disposition).

## Isolation
If your harness lets the agent run its own code (most do), run episodes under
`DockerSandbox` — an application-level file sandbox cannot contain a process that writes and
executes code. The `minimal` harness is trusted and uses `LocalSandbox`.

## Checklist
- [ ] surfaces declared as data
- [ ] `seed` produces a runnable episode-0 harness
- [ ] disposition install is removable
- [ ] episode emits a normalized `ActionEvent` trace via `read_trace`
- [ ] real (code-running) harness runs under `DockerSandbox`
