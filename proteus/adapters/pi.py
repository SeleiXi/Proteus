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

IMAGE = os.environ.get("PROTEUS_PI_IMAGE", "proteus-env-pi:0.84.2")
PHASE_TIMEOUT_S = 600
#: pi's own installed package: dist/ (~11MB readable JS with comments) is the editable
#: self; node_modules (~129MB of dependencies) stays in the image as fixed apparatus.
PKG_PATH = "/usr/local/lib/node_modules/@earendil-works/pi-coding-agent"
SELF_CODE = ("dist", "package.json")

SEED_INSTRUCTIONS = """\
# Agent instructions

You maintain and improve your own harness — the files in this workspace, which persist
across sessions. Your surfaces:

- `AGENTS.md` — these instructions (you may refine them)
- `notes/` — markdown knowledge you want future sessions to have
- `tools/` — small python utilities you may want later
- `skills/` — pi skill files (loaded automatically next session)
- `src/` — your own program: the code that runs you each session boots from here.
  You may edit it; the next session runs whatever you leave. If it stops booting,
  the run ends.

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
                 phase_timeout_s: int = PHASE_TIMEOUT_S,
                 pkg_path: str = PKG_PATH) -> None:
        self.image = image
        self.network = network
        self.provider = provider
        self.model = model
        self.phase_timeout_s = phase_timeout_s
        self.pkg_path = pkg_path
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
        """Copy pi's own package code out of the image into `dest` (see DshHarness)."""
        if dest.exists() and any(dest.iterdir()):
            return
        dest.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "none",
             "-v", f"{dest}:/proteus-out", "--entrypoint", "sh", self.image,
             "-c", "cp -r " + " ".join(f"{self.pkg_path}/{p}" for p in SELF_CODE)
                   + " /proteus-out/"],
            capture_output=True, text=True, errors="replace", check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                f"could not extract pi self-code from {self.image}: {proc.stderr[-300:]}")

    def _shadow_mounts(self, harness: Path) -> tuple:
        src = harness / "src"
        return tuple((str(src / p), f"{self.pkg_path}/{p}")
                     for p in SELF_CODE if (src / p).exists())

    def check_boot(self, harness_root: Path) -> str:
        """Viability gate: the seed's own code must still print a version (see DshHarness)."""
        harness = Path(harness_root)
        proc = self.sandbox.run(
            harness.parent, ["--version"], env={}, timeout_s=60,
            mounts=((str(harness), "/workspace"),) + self._shadow_mounts(harness))
        if proc.returncode != 0:
            return (f"self-edited loop does not boot (exit {proc.returncode}): "
                    f"{(proc.stderr or proc.stdout)[-300:]}")
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
        if (harness / "src").is_dir():
            error = self.check_boot(harness)
        for phase in PHASES if not error else ():
            before = self._sessions(state)
            try:
                proc = self.sandbox.run(
                    run_root,
                    ["--provider", self.provider, "--model", spec.model or self.model,
                     "--session-dir", "/state", "--skill", "/workspace/skills",
                     "-p", spec.phase_prompts.get(phase, phase)],
                    env={"DEEPSEEK_API_KEY": self.key},
                    timeout_s=self.phase_timeout_s,
                    mounts=((str(harness), "/workspace"), (str(state), "/state"))
                           + self._shadow_mounts(harness),
                )
            except subprocess.TimeoutExpired:
                error = f"phase {phase}: timeout after {self.phase_timeout_s}s"
                break
            if proc.returncode != 0:
                error = f"phase {phase}: exit {proc.returncode}: {proc.stderr[-400:]}"
                break
            new = self._sessions(state) - before
            if new:
                mapping[phase] = min(new).name
        (run_root / "traces" / f"ep{spec.episode:03d}.json").write_text(
            json.dumps(mapping, indent=1))
        trace = self.read_trace(run_root, spec.episode)
        return EpisodeResult(
            episode=spec.episode, ok=not error,
            turns=sum(1 for e in trace if e.tool), error=error,
            counters={"phases": len(mapping)},
        )

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
