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

import hashlib
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
DISPOSITION_OPEN = "<!-- proteus:disposition -->"
DISPOSITION_CLOSE = "<!-- /proteus:disposition -->"

SEED_INSTRUCTIONS = """\
# Agent instructions

You maintain and improve your own harness — the files in this workspace, which persist
across sessions. Your surfaces:

- `AGENTS.md` — these instructions (you may refine them)
- `notes/` — markdown knowledge you want future sessions to have
- `tools/` — small python utilities you may want later

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

    SURFACES = (
        Surface("instructions", "AGENTS.md", unit="file", free_named=False),
        Surface("notes", "notes", unit="file", write_tools=frozenset({"write"})),
        Surface("tools", "tools", unit="file", write_tools=frozenset({"write"}),
                is_code=True),
    )

    def __init__(self, image: str = IMAGE, network: str = "host") -> None:
        self.image = image
        self.network = network
        self.key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_KEY", "")
        from proteus.sandbox import DockerSandbox, SandboxConfig
        self.sandbox = DockerSandbox(SandboxConfig(
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

    def install_disposition(self, harness_root: Path, disposition: Disposition) -> None:
        path = harness_root / "AGENTS.md"
        text = path.read_text(encoding="utf-8")
        # remove any previous block, then append the new one (NEUTRAL appends nothing)
        if DISPOSITION_OPEN in text:
            head, _, rest = text.partition(DISPOSITION_OPEN)
            _, _, tail = rest.partition(DISPOSITION_CLOSE)
            text = head + tail
        if not disposition.is_empty and disposition.prompt_suffix:
            text = (text.rstrip() + "\n\n" + DISPOSITION_OPEN + "\n"
                    + disposition.prompt_suffix.strip() + "\n" + DISPOSITION_CLOSE + "\n")
        path.write_text(text, encoding="utf-8")

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
        for phase in PHASES:
            before = self._session_dirs(state)
            try:
                proc = self.sandbox.run(
                    run_root,
                    ["--profile", "headless", spec.phase_prompts.get(phase, phase)],
                    env={"DEEPSEEK_API_KEY": self.key,
                         "DSH_PERMISSION_MODE": "workspace-write"},
                    timeout_s=PHASE_TIMEOUT_S,
                    mounts=((str(harness), "/workspace"), (str(state), "/state")),
                )
            except subprocess.TimeoutExpired:
                error = f"phase {phase}: timeout after {PHASE_TIMEOUT_S}s"
                break
            if proc.returncode != 0:
                error = f"phase {phase}: exit {proc.returncode}: {proc.stderr[-400:]}"
                break
            new = self._session_dirs(state) - before
            if new:
                mapping[phase] = str(sorted(new)[0].relative_to(state))
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
        path = Path(harness_root) / "AGENTS.md"
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        if DISPOSITION_OPEN in text:
            block = text.split(DISPOSITION_OPEN, 1)[1].split(DISPOSITION_CLOSE, 1)[0]
        else:
            block = ""
        return hashlib.sha256(block.encode()).hexdigest()[:16]
