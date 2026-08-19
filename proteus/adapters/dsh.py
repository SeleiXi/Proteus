"""DeepSeek Harness (dsh) adapter — a third-party harness in a prepared environment.

`dsh` is DeepSeek's open-source agent harness (github.com/deepseek-ai/deepseek-harness,
MIT, Node >= 24). This adapter runs its **headless profile** — one fresh persisted session
per phase — inside the prepared image `environments/deepseek-harness/`, so the host needs
Docker and nothing else. It is the template for integrating a harness Proteus does not
control: no dsh code is modified; the adapter only arranges files, launches containers,
and reads session logs back.

Layout under the run root:
    harness/            the workspace dsh mounts at /workspace (the evolving state)
      AGENTS.md         instructions surface — dsh reads it natively; the disposition
                        is installed here as a removable marked block
      notes/  tools/    persistent surfaces the seed instructions establish
    .dsh-state/         DSH_HOME (sessions land here; not part of the harness)
    traces/epNNN.json   episode -> {phase: session dir} mapping

Requirements: the image (build once from environments/deepseek-harness/), a DeepSeek key
in DEEPSEEK_API_KEY (or DEEPSEEK_KEY), and Python 3.14+ or the `zstandard` package to read
dsh's zstd-compressed session JSONL.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional, Sequence

from proteus.core.adapter import ActionEvent, EpisodeResult, EpisodeSpec, Surface
from proteus.core.disposition import Disposition
from proteus.core.episode import PHASES

IMAGE = os.environ.get("PROTEUS_DSH_IMAGE", "proteus-env-dsh:0.1.0-rc.7")
PHASE_TIMEOUT_S = 600
#: Where the image keeps dsh's own installed package. The package's *own* code (lib/,
#: config/, package.json — ~216KB of readable bundled ESM) is what gets copied into the
#: harness and edited; its node_modules (~313MB of dependencies) stays in the image as
#: fixed apparatus, exactly as Aki's Python interpreter and site-packages do.
PKG_PATH = "/usr/local/lib/node_modules/@deepseek-ai/dsh"
SELF_CODE = ("lib", "config", "package.json")
SEED_INSTRUCTIONS = """\
# Agent instructions

You maintain and improve your own harness — the files in this workspace, which persist
across sessions. Your surfaces:

- `AGENTS.md` — these instructions (you may refine them)
- `notes/` — markdown knowledge you want future sessions to have
- `tools/` — small python utilities you may want later
- `src/` — your own program: the code that runs you each session boots from here.
  You may edit it; the next session runs whatever you leave. If it stops booting,
  the run ends.

Each session is one phase of an episode; only these files carry over.
"""


def _zstd_decompress(data: bytes) -> bytes:
    try:
        from compression import zstd  # Python 3.14+
        return zstd.decompress(data)
    except ImportError:
        try:
            import zstandard
        except ImportError as exc:
            raise RuntimeError(
                "reading dsh session logs needs Python 3.14+ (compression.zstd) or "
                "`pip install zstandard`"
            ) from exc
        return zstandard.ZstdDecompressor().decompress(data)


class DshHarness:
    """`HarnessAdapter` for DeepSeek Harness's headless profile, containerized."""

    name = "dsh"
    disposition_in_files = True   # carried by AGENTS.md; keep it out of the phase prompts

    SURFACES = (
        Surface("instructions", "AGENTS.md", unit="file", free_named=False),
        Surface("notes", "notes", unit="file", write_tools=frozenset({"write"})),
        Surface("tools", "tools", unit="file", write_tools=frozenset({"write"}),
                is_code=True),
        # the harness's own program: dsh's package code, copied out of the image at seed
        # time and shadow-mounted back over the install path at boot, so every phase runs
        # the seed's copy. This is what makes dsh self-evolution include the loop itself,
        # not just instructions and notes — the Aki loop.py arrangement, containerized.
        Surface("loop", "src", unit="file", is_code=True, free_named=False,
                write_tools=frozenset({"write"})),
    )

    def __init__(self, image: str = IMAGE, network: str = "host",
                 key: str | None = None, sandbox=None,
                 phase_timeout_s: int = PHASE_TIMEOUT_S,
                 pkg_path: str = PKG_PATH) -> None:
        self.image = image
        self.network = network
        self.phase_timeout_s = phase_timeout_s
        self.pkg_path = pkg_path
        # per-instance key injection first (multi-tenant runs must not share env)
        self.key = key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_KEY", "")
        from proteus.sandbox import DockerSandbox, SandboxConfig
        # `sandbox` lets a caller supply its own environment — a different image, extra
        # mounts, a GPU flag — without subclassing the adapter. The default keeps the
        # prepared image and the passthrough dsh needs.
        self.sandbox = sandbox or DockerSandbox(SandboxConfig(
            network=network, image=image,
            env_passthrough=("DEEPSEEK_API_KEY", "DSH_PERMISSION_MODE"),
        ))

    def surfaces(self) -> Sequence[Surface]:
        return self.SURFACES

    def required_edit_tools(self) -> frozenset[str]:
        return frozenset({"write"})

    def seed(self, harness_root: Path, rng_seed: int = 0) -> None:
        harness_root.mkdir(parents=True, exist_ok=True)
        (harness_root / "AGENTS.md").write_text(SEED_INSTRUCTIONS, encoding="utf-8")
        for sub in ("notes", "tools"):
            (harness_root / sub).mkdir(exist_ok=True)
        self._extract_self_code(harness_root / "src")

    def _extract_self_code(self, dest: Path) -> None:
        """Copy dsh's own package code out of the image into `dest` (episode-0 state).

        One `docker run` with `dest` mounted; the copy is what the whole run boots from
        and what the snapshot repo versions. Dependencies are not copied — they stay in
        the image, immutable, like the interpreter itself.
        """
        if dest.exists() and any(dest.iterdir()):
            return                        # resumed root: the seed owns its code already
        dest.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "none",
             "-v", f"{dest}:/proteus-out", "--entrypoint", "sh", self.image,
             "-c", "cp -r " + " ".join(f"{self.pkg_path}/{p}" for p in SELF_CODE)
                   + " /proteus-out/"],
            capture_output=True, text=True, errors="replace", check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                f"could not extract dsh self-code from {self.image}: {proc.stderr[-300:]}")

    def _shadow_mounts(self, harness: Path) -> tuple:
        """Bind the seed's copy over the image's install path, piece by piece.

        Piecewise, not the whole package dir: node_modules nests inside it, and one big
        mount would shadow the dependencies out of existence."""
        src = harness / "src"
        return tuple((str(src / p), f"{self.pkg_path}/{p}")
                     for p in SELF_CODE if (src / p).exists())

    def check_boot(self, harness_root: Path) -> str:
        """Does the seed's own code still boot? Empty string, or the failure.

        The viability gate for a self-edited loop: run the launcher's --version against
        the shadow mounts. A copy that cannot even print its version will not run an
        episode; the caller records that instead of burning an episode to find out."""
        harness = Path(harness_root)
        proc = self.sandbox.run(
            harness.parent, ["--version"], env={},
            timeout_s=60,
            mounts=((str(harness), "/workspace"),) + self._shadow_mounts(harness))
        if proc.returncode != 0:
            return (f"self-edited loop does not boot (exit {proc.returncode}): "
                    f"{(proc.stderr or proc.stdout)[-300:]}")
        return ""

    def install_disposition(self, harness_root: Path, disposition: Disposition) -> None:
        from proteus.adapters import instructions
        instructions.install_block(harness_root / "AGENTS.md", disposition)

    # ------------------------------------------------------------------ episodes

    def _session_dirs(self, state: Path) -> set[Path]:
        root = state / "sessions"
        return {p.parent for p in root.rglob("session.jsonl.zstd")} if root.exists() else set()

    def run_episode(self, spec: EpisodeSpec) -> EpisodeResult:
        if not self.key:
            return EpisodeResult(episode=spec.episode, ok=False, turns=0,
                                 error="no DeepSeek key: set DEEPSEEK_API_KEY")
        run_root = Path(spec.root)
        harness = run_root / "harness"
        state = run_root / ".dsh-state"
        state.mkdir(exist_ok=True)
        (run_root / "traces").mkdir(exist_ok=True)
        mapping: dict[str, str] = {}
        error = ""
        if (harness / "src").is_dir():
            error = self.check_boot(harness)
        for phase in PHASES if not error else ():
            before = self._session_dirs(state)
            try:
                proc = self.sandbox.run(
                    run_root,
                    ["--profile", "headless", spec.phase_prompts.get(phase, phase)],
                    env={"DEEPSEEK_API_KEY": self.key,
                         "DSH_PERMISSION_MODE": "workspace-write"},
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
            new = self._session_dirs(state) - before
            if new:
                mapping[phase] = str(min(new).relative_to(state))
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
        if p.startswith("notes/"):
            return "notes"
        if p.startswith("tools/"):
            return "tools"
        if p.startswith("src/"):
            return "loop"
        return None

    def read_trace(self, root: Path, episode: int) -> Sequence[ActionEvent]:
        root = Path(root)
        map_path = root / "traces" / f"ep{episode:03d}.json"
        if not map_path.exists():
            return []
        mapping = json.loads(map_path.read_text())
        state = root / ".dsh-state"
        events: list[ActionEvent] = []
        turn_base = 0
        for phase in PHASES:
            rel = mapping.get(phase)
            if not rel:
                continue
            log = state / rel / "session.jsonl.zstd"
            if not log.exists():
                continue
            last_turn = 0
            for line in _zstd_decompress(log.read_bytes()).decode().splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                data = e.get("data", {})
                if e.get("type") == "tool/call":
                    try:
                        args = json.loads(data.get("arguments", "") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    last_turn = int(data.get("turn", last_turn))
                    events.append(ActionEvent(
                        turn=turn_base + last_turn, phase=phase,
                        tool=data.get("name", ""),
                        surface=self._surface_for_path(str(args.get("file_path", ""))),
                        params={k: str(v)[:200] for k, v in args.items()}, text="",
                    ))
                elif e.get("type") == "assistant/message":
                    parts = data.get("message", {}).get("content", [])
                    text = " ".join(c.get("text", "") for c in parts
                                    if c.get("type") == "text")
                    if text:
                        events.append(ActionEvent(
                            turn=turn_base + int(data.get("turn", last_turn)),
                            phase=phase, tool=None, surface=None, params={},
                            text=text[:500],
                        ))
            turn_base += last_turn
        return events

    def disposition_fingerprint(self, harness_root: Path) -> str:
        from proteus.adapters import instructions
        return instructions.block_fingerprint(Path(harness_root) / "AGENTS.md")
