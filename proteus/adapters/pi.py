"""Pi adapter — Mario Zechner's minimal coding harness (pi-mono) in a prepared container.

Pi (github.com/badlogic/pi-mono, npm `@earendil-works/pi-coding-agent`) is the minimal
end of the harness spectrum: four built-in tools (read/write/edit/bash), native
`AGENTS.md` context loading, native skills. That makes it the cleanest demonstration that
the adapter contract covers real third-party harnesses of any size — the whole adapter is
symmetric with `dsh.py` and shares its disposition carrier.

Per phase, one non-interactive pi session (`-p`) runs in the prepared image
(`environments/pi/`), with the workspace at /workspace and session storage at /state
(`--session-dir`). The trace is parsed from pi's session JSONL (v3: `message` events whose
content blocks carry `toolCall` entries). Skills are loaded explicitly with
`--skill /workspace/skills`, so the skills surface is version-robust rather than relying
on discovery conventions.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional, Sequence

from proteus.adapters import instructions
from proteus.core.adapter import ActionEvent, EpisodeResult, EpisodeSpec, Surface
from proteus.core.disposition import Disposition
from proteus.core.episode import PHASES

IMAGE = os.environ.get("PROTEUS_PI_IMAGE", "proteus-env-pi-src:0.84.2")
PHASE_TIMEOUT_S = 600
#: The editable self is pi's real TypeScript source (~1,100 .ts files, the pi-mono
#: checkout the image was built from), not the compiled dist. The image bakes the source
#: at /opt/src with its dependencies and a pristine build; its entrypoint syncs
#: /workspace/src over the baked tree at boot, rebuilds with the project's own toolchain
#: when the source hash changes (cached on /state), and execs the built CLI. See
#: environments/pi-src/.
SOURCE_TAR = "/opt/pi-source.tar"

SEED_INSTRUCTIONS = """\
# Agent instructions

You maintain and improve your own harness — the files in this workspace, which persist
across sessions. Your surfaces:

- `AGENTS.md` — these instructions (you may refine them)
- `notes/` — markdown knowledge you want future sessions to have
- `tools/` — small python utilities you may want later
- `skills/` — pi skill files (loaded automatically next session)
- `src/` — your own program: the real TypeScript source of the agent that runs you.
  Edit it and the next session is rebuilt from your edit and runs it. If your edit
  does not compile, the run ends.

Each session is one phase of an episode; only these files carry over.
"""


class PiHarness:
    """`HarnessAdapter` for pi-coding-agent's non-interactive mode, containerized."""

    name = "pi"
    disposition_in_files = True   # carried by AGENTS.md; keep it out of the phase prompts

    SURFACES = (
        Surface("instructions", "AGENTS.md", unit="file", free_named=False),
        Surface("skills", "skills", unit="file", write_tools=frozenset({"write", "edit"})),
        Surface("notes", "notes", unit="file", write_tools=frozenset({"write", "edit"})),
        Surface("tools", "tools", unit="file", write_tools=frozenset({"write", "edit"}),
                is_code=True),
        # the harness's own program, shadow-mounted over the install path at boot — see
        # DshHarness: same arrangement, same reasons
        Surface("loop", "src", unit="file", is_code=True, free_named=False,
                write_tools=frozenset({"write", "edit"})),
    )

    def __init__(self, image: str = IMAGE, network: str = "host",
                 provider: str = "deepseek", model: str = "deepseek-v4-flash",
                 key: str | None = None, sandbox=None,
                 phase_timeout_s: int = PHASE_TIMEOUT_S) -> None:
        self.image = image
        self.network = network
        self.provider = provider
        self.model = model
        self.phase_timeout_s = phase_timeout_s
        # per-instance key injection first (multi-tenant runs must not share env)
        self.key = key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_KEY", "")
        from proteus.sandbox import DockerSandbox, SandboxConfig
        # a caller may pass its own environment (see DshHarness.__init__)
        self.sandbox = sandbox or DockerSandbox(SandboxConfig(
            network=network, image=image, env_passthrough=("DEEPSEEK_API_KEY",),
        ))

    def surfaces(self) -> Sequence[Surface]:
        return self.SURFACES

    def required_edit_tools(self) -> frozenset[str]:
        return frozenset({"write", "edit"})

    def seed(self, harness_root: Path, rng_seed: int = 0) -> None:
        harness_root.mkdir(parents=True, exist_ok=True)
        (harness_root / "AGENTS.md").write_text(SEED_INSTRUCTIONS, encoding="utf-8")
        for sub in ("notes", "tools", "skills"):
            (harness_root / sub).mkdir(exist_ok=True)
        self._extract_self_code(harness_root / "src")

    def _extract_self_code(self, dest: Path) -> None:
        """Unpack the source the image was built from into `dest` (episode-0 state).

        The image bakes a source-only tar at build time precisely so this is cheap and
        exact: what the seed gets is byte-for-byte the source of the build it boots."""
        if dest.exists() and any(dest.iterdir()):
            return                        # resumed root: the seed owns its source already
        dest.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "none",
             "-v", f"{dest}:/proteus-out", "--entrypoint", "sh", self.image,
             "-c", f"tar -xf {SOURCE_TAR} -C /proteus-out --strip-components=1"],
            capture_output=True, text=True, errors="replace", check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                f"could not extract pi source from {self.image}: {proc.stderr[-300:]}")

    def check_boot(self, harness_root: Path) -> str:
        """Viability gate: sync + rebuild + `--version` through the image's boot wrapper.

        Because the wrapper rebuilds from /workspace/src, a type error the agent wrote
        into its own source surfaces here as a legible build failure (exit 97 with the
        build log tail), before any API spend."""
        harness = Path(harness_root)
        state = harness.parent / ".pi-state"
        state.mkdir(exist_ok=True)
        proc = self.sandbox.run(
            harness.parent, ["--version"], env={}, timeout_s=300,
            mounts=((str(harness), "/workspace"), (str(state), "/state")))
        if proc.returncode != 0:
            return (f"self-edited source does not boot (exit {proc.returncode}): "
                    f"{(proc.stderr or proc.stdout)[-400:]}")
        return ""

    def install_disposition(self, harness_root: Path, disposition: Disposition) -> None:
        instructions.install_block(harness_root / "AGENTS.md", disposition)

    def disposition_fingerprint(self, harness_root: Path) -> str:
        return instructions.block_fingerprint(Path(harness_root) / "AGENTS.md")

    # ------------------------------------------------------------------ episodes

    @staticmethod
    def _sessions(state: Path) -> set[Path]:
        return set(state.glob("*.jsonl"))

    def run_episode(self, spec: EpisodeSpec) -> EpisodeResult:
        if not self.key:
            return EpisodeResult(episode=spec.episode, ok=False, turns=0,
                                 error="no DeepSeek key: set DEEPSEEK_API_KEY")
        run_root = Path(spec.root)
        harness = run_root / "harness"
        state = run_root / ".pi-state"
        state.mkdir(exist_ok=True)
        (run_root / "traces").mkdir(exist_ok=True)
        mapping: dict[str, str] = {}
        error = ""
        capped = False
        budget = int(spec.max_turns or 0)
        episode_files: set = set()
        if (harness / "src").is_dir():
            error = self.check_boot(harness)
        for phase in PHASES if not error else ():
            # budget enforcement, both layers (see DshHarness.run_episode): exact between
            # phases, approximate mid-phase via the live session log
            if budget and self._live_calls(state, episode_files, set()) >= budget:
                capped = True
                break
            before = self._sessions(state)
            fired = [False]

            def stop_check(before=before, fired=fired):
                if self._live_calls(state, episode_files,
                                    self._sessions(state) - before) >= budget:
                    fired[0] = True
                    return True
                return False

            try:
                proc = self.sandbox.run(
                    run_root,
                    ["--provider", self.provider, "--model", spec.model or self.model,
                     "--session-dir", "/state", "--skill", "/workspace/skills",
                     "-p", spec.phase_prompts.get(phase, phase)],
                    env={"DEEPSEEK_API_KEY": self.key},
                    timeout_s=self.phase_timeout_s,
                    mounts=((str(harness), "/workspace"), (str(state), "/state")),
                    stop_check=stop_check if budget else None,
                )
            except subprocess.TimeoutExpired:
                error = f"phase {phase}: timeout after {self.phase_timeout_s}s"
                break
            new = self._sessions(state) - before
            if new:
                mapping[phase] = min(new).name
                episode_files |= new
            if proc.returncode != 0:
                if fired[0]:
                    capped = True      # stopped by the budget: a cap, not a failure
                    break
                error = f"phase {phase}: exit {proc.returncode}: {proc.stderr[-400:]}"
                break
        (run_root / "traces" / f"ep{spec.episode:03d}.json").write_text(
            json.dumps(mapping, indent=1))
        trace = self.read_trace(run_root, spec.episode)
        return EpisodeResult(
            episode=spec.episode, ok=not error,
            turns=sum(1 for e in trace if e.tool), error=error,
            counters={"phases": len(mapping), "turn_capped": capped},
        )

    def _live_calls(self, state: Path, episode_files: set, extra: set) -> int:
        """Tool calls made so far this episode, read live from the session logs.

        pi's session JSONL is plain text and appended per event, so a mid-phase read
        sees every call already made. Counted by marker substring — over-counting stops
        early, which is the conservative direction for a budget."""
        n = 0
        for f in set(episode_files) | set(extra):
            path = Path(f) if isinstance(f, Path) else state / f
            try:
                n += path.read_text(encoding="utf-8", errors="replace").count('"toolCall"')
            except OSError:
                continue
        return n

    # ------------------------------------------------------------------ measure path

    def _surface_for_path(self, file_path: str) -> Optional[str]:
        p = file_path.replace("/workspace/", "")
        if p == "AGENTS.md":
            return "instructions"
        for s in ("skills", "notes", "tools"):
            if p.startswith(f"{s}/"):
                return s
        if p.startswith("src/"):
            return "loop"
        return None

    def read_trace(self, root: Path, episode: int) -> Sequence[ActionEvent]:
        root = Path(root)
        map_path = root / "traces" / f"ep{episode:03d}.json"
        if not map_path.exists():
            return []
        mapping = json.loads(map_path.read_text())
        state = root / ".pi-state"
        events: list[ActionEvent] = []
        turn = 0
        for phase in PHASES:
            name = mapping.get(phase)
            if not name or not (state / name).exists():
                continue
            for line in (state / name).read_text(encoding="utf-8",
                                                 errors="replace").splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") != "message":
                    continue
                msg = e.get("message", {})
                if msg.get("role") != "assistant":
                    continue
                turn += 1
                for block in msg.get("content", []):
                    btype = block.get("type", "")
                    if btype in ("toolCall", "tool_call", "toolUse"):
                        args = block.get("arguments") or block.get("input") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        path = str(args.get("file_path") or args.get("path") or "")
                        events.append(ActionEvent(
                            turn=turn, phase=phase,
                            tool=block.get("name", ""),
                            surface=self._surface_for_path(path),
                            params={k: str(v)[:200] for k, v in args.items()}, text="",
                        ))
                    elif btype == "text" and block.get("text"):
                        events.append(ActionEvent(
                            turn=turn, phase=phase, tool=None, surface=None,
                            params={}, text=block["text"][:500],
                        ))
        return events
