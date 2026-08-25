"""OpenAI Codex source-evolving adapter.

The prepared image contains a pinned openai/codex checkout and pristine Rust build.
Each run receives that exact source under ``harness/src``.  Episodes execute a frozen
active snapshot while writing to a separate candidate; the candidate is compiled and
version-probed at the episode boundary before it can become active.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional, Sequence

from proteus.adapters import instructions
from proteus.core.adapter import ActionEvent, EpisodeResult, EpisodeSpec, Surface
from proteus.core.budget import PHASES, budget_plan, phase_prompt
from proteus.core.continuity import CONTAINER_ROOT, HandoffStore
from proteus.core.disposition import Disposition

IMAGE = os.environ.get("PROTEUS_CODEX_IMAGE", "proteus-env-codex-src:ff29a443")
SOURCE_TAR = "/opt/codex-source.tar"
PHASE_TIMEOUT_S = 900
DEFAULT_MODEL = os.environ.get("PROTEUS_CODEX_MODEL", "gpt-5.5")

SEED_INSTRUCTIONS = """\
# Agent instructions

You are OpenAI Codex running inside a Proteus evolution episode. The currently active
harness is frozen and read-only at `/workspace`; persistent candidate edits must go to
`/workspace/candidate`. Never modify or execute candidate harness source during the same
episode; Proteus compiles and validates it after reflect, then activates it next episode.

Persistent candidate surfaces:

- `/workspace/candidate/AGENTS.md` — these instructions
- `/workspace/candidate/notes/` — durable knowledge
- `/workspace/candidate/tools/` — small utilities
- `/workspace/candidate/skills/` — Codex skills
- `/workspace/candidate/src/` — the real open-source Codex Rust workspace

Read and replace `/workspace/.proteus/handoff.md` as each phase requests. Do not place
credentials, raw reasoning, or raw tool output in the handoff.
"""


class CodexHarness:
    """Containerized adapter for a source-built Codex CLI."""

    name = "codex"
    continuity_mode = "framework"
    staged_activation = True
    disposition_in_files = True

    SURFACES = (
        Surface("instructions", "AGENTS.md", unit="file", free_named=False),
        Surface("skills", "skills", unit="file",
                write_tools=frozenset({"apply_patch", "file_change"})),
        Surface("notes", "notes", unit="file",
                write_tools=frozenset({"apply_patch", "file_change"})),
        Surface("tools", "tools", unit="file", is_code=True,
                write_tools=frozenset({"apply_patch", "file_change"})),
        Surface("loop", "src", unit="file", is_code=True, free_named=False,
                write_tools=frozenset({"apply_patch", "file_change"})),
    )

    def __init__(self, image: str = IMAGE, network: str = "host",
                 model: str = DEFAULT_MODEL, auth_home: Path | None = None,
                 sandbox=None, phase_timeout_s: int = PHASE_TIMEOUT_S) -> None:
        self.image = image
        self.network = network
        self.model = model
        self.auth_home = Path(auth_home or Path.home() / ".codex")
        self.phase_timeout_s = phase_timeout_s
        proxy = os.environ.get("PROTEUS_CODEX_PROXY", "")
        self.proxy_env = {
            key: os.environ.get(key, proxy)
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
            if os.environ.get(key, proxy)
        }
        from proteus.sandbox import DockerSandbox, SandboxConfig
        host_user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else ""
        self.sandbox = sandbox or DockerSandbox(SandboxConfig(
            network=network, image=image, user=host_user,
            env_passthrough=tuple(self.proxy_env),
        ))
        # Boundary compilation writes into the image's root-owned, prebuilt Cargo
        # target.  It therefore runs as container root, while all model-driven phases
        # keep running as the host uid/gid so candidate files remain host-owned.
        self.build_sandbox = sandbox or DockerSandbox(SandboxConfig(
            network=network, image=image,
            env_passthrough=tuple(self.proxy_env),
        ))

    def surfaces(self) -> Sequence[Surface]:
        return self.SURFACES

    def required_edit_tools(self) -> frozenset[str]:
        return frozenset({"apply_patch", "file_change"})

    def seed(self, harness_root: Path, rng_seed: int = 0) -> None:
        harness_root.mkdir(parents=True, exist_ok=True)
        (harness_root / "AGENTS.md").write_text(SEED_INSTRUCTIONS, encoding="utf-8")
        for sub in ("notes", "tools", "skills"):
            (harness_root / sub).mkdir(exist_ok=True)
        self._extract_self_code(harness_root / "src")

    def _extract_self_code(self, dest: Path) -> None:
        dest = dest.resolve()
        if dest.exists() and any(dest.iterdir()):
            return
        dest.mkdir(parents=True, exist_ok=True)
        user = (["--user", f"{os.getuid()}:{os.getgid()}"]
                if hasattr(os, "getuid") else [])
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", *user,
             "-v", f"{dest}:/proteus-out", "--entrypoint", "sh", self.image,
             "-c", f"tar -xf {SOURCE_TAR} -C /proteus-out"],
            capture_output=True, text=True, errors="replace", check=False,
        )
        if proc.returncode:
            raise RuntimeError(
                f"could not extract Codex source from {self.image}: {proc.stderr[-400:]}")

    def install_disposition(self, harness_root: Path, disposition: Disposition) -> None:
        instructions.install_block(Path(harness_root) / "AGENTS.md", disposition)

    def disposition_fingerprint(self, harness_root: Path) -> str:
        return instructions.block_fingerprint(Path(harness_root) / "AGENTS.md")

    def check_boot(self, harness_root: Path) -> str:
        harness = Path(harness_root).resolve()
        state = harness.parent / ".codex-build-state"
        state.mkdir(exist_ok=True)
        proc = self.build_sandbox.run(
            harness.parent, ["--version"], env={}, timeout_s=1800,
            mounts=((str(harness), "/workspace"), (str(state), "/state")),
        )
        if proc.returncode:
            output = proc.stderr or proc.stdout
            return (f"self-edited Codex source does not boot (exit {proc.returncode}): "
                    f"{output[-800:]}")
        return ""

    def validate_candidate(self, harness_root: Path) -> str:
        return self.check_boot(harness_root)

    @staticmethod
    def _task_mount(run_root: Path) -> tuple:
        task = run_root / "task"
        return ((str(task), "/workspace/task"),) if task.is_dir() else ()

    @staticmethod
    def _trace_path(run_root: Path, episode: int, phase: str) -> Path:
        return run_root / "traces" / f"ep{episode:03d}-{phase}.jsonl"

    def run_episode(self, spec: EpisodeSpec) -> EpisodeResult:
        auth = self.auth_home / "auth.json"
        if not auth.is_file():
            return EpisodeResult(
                spec.episode, False, 0, "Codex is not logged in: auth.json missing")
        run_root = Path(spec.root).resolve()
        harness = run_root / "harness"
        state = run_root / ".codex-state"
        state.mkdir(exist_ok=True)
        traces = run_root / "traces"
        traces.mkdir(exist_ok=True)
        handoffs = HandoffStore(run_root)
        active = (Path(spec.active_root).resolve()
                  if spec.active_root is not None else harness)
        if spec.active_root is None and (harness / "src").is_dir():
            error = self.check_boot(harness)
            if error:
                return EpisodeResult(spec.episode, False, 0, error)
        if spec.active_root is not None:
            for sub in ("candidate", ".proteus"):
                (active / sub).mkdir(exist_ok=True)
            if (run_root / "task").is_dir():
                (active / "task").mkdir(exist_ok=True)
        workspace_mounts = (
            (str(active), "/workspace", "ro"),
            (str(harness), "/workspace/candidate"),
        ) if spec.active_root is not None else ((str(harness), "/workspace"),)
        plan = budget_plan(spec)
        used = 0
        error = ""
        phase_count = 0
        for phase in PHASES:
            if plan.enabled and used >= plan.hard_limit:
                break
            stop_at = plan.stop_at(phase, used)
            if plan.enabled and used >= stop_at:
                continue
            handoff_start = handoffs.begin(spec.episode, phase)
            trace = self._trace_path(run_root, spec.episode, phase)
            args = ["--proteus-trace", f"/records/{trace.name}",
                    "-c", "features.code_mode_host=false",
                    "exec", "--json", "--dangerously-bypass-approvals-and-sandbox",
                    "--skip-git-repo-check", "--ephemeral", "-C", "/workspace"]
            chosen_model = spec.model or self.model
            if chosen_model:
                args += ["--model", chosen_model]
            args += [phase_prompt(spec, phase, used)]
            build_state = run_root / ".codex-build-state"
            build_state.mkdir(exist_ok=True)
            mounts = workspace_mounts + (
                (str(state), "/codex-state"),
                (str(build_state), "/state"),
                (str(traces), "/records"),
                (str(handoffs.root), CONTAINER_ROOT),
                (str(auth), "/run/proteus-codex-auth.json", "ro"),
            ) + self._task_mount(run_root)
            fired = [False]

            def stop_check() -> bool:
                current = used + sum(
                    1 for event in self._read_native_trace(trace, phase) if event.tool)
                if current >= stop_at:
                    fired[0] = True
                    return True
                return False

            try:
                proc = self.sandbox.run(run_root, args, env=self.proxy_env,
                                        timeout_s=self.phase_timeout_s, mounts=mounts,
                                        stop_check=stop_check if plan.enabled else None)
            except subprocess.TimeoutExpired:
                error = f"phase {phase}: timeout after {self.phase_timeout_s}s"
                break
            phase_events = self._read_native_trace(trace, phase)
            handoffs.finish(handoff_start, phase_events, interrupted=proc.returncode != 0)
            used += sum(1 for event in phase_events if event.tool)
            phase_count += 1
            if proc.returncode:
                if fired[0]:
                    if plan.enabled and used >= plan.hard_limit:
                        break
                    continue
                error = (f"phase {phase}: exit {proc.returncode}: "
                         f"{(proc.stderr or proc.stdout)[-500:]}")
                break
        events = self.read_trace(run_root, spec.episode)
        return EpisodeResult(spec.episode, not error,
                             sum(1 for event in events if event.tool), error,
                             {"phases": phase_count, "tool_calls": used})

    def _surface_for_path(self, raw: str) -> Optional[str]:
        path = raw.replace("\\", "/")
        for prefix in ("/workspace/candidate/", "/workspace/", "candidate/"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        if path == "AGENTS.md":
            return "instructions"
        for surface in ("skills", "notes", "tools"):
            if path.startswith(surface + "/"):
                return surface
        return "loop" if path.startswith("src/") else None

    def _read_native_trace(self, path: Path, phase: str) -> list[ActionEvent]:
        events: list[ActionEvent] = []
        turn = 0
        if not path.exists():
            return events
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if raw.get("type") != "item.completed":
                continue
            item = raw.get("item") or {}
            kind = item.get("type", "")
            turn += 1
            if kind == "agent_message":
                text = item.get("text", "")
                events.append(ActionEvent(turn, phase, None, None, {}, text[:500]))
                continue
            if kind in ("command_execution", "mcp_tool_call", "file_change"):
                tool = {"command_execution": "bash", "file_change": "apply_patch"}.get(
                    kind, item.get("tool", item.get("name", "mcp")))
                changes = item.get("changes") or []
                path_arg = ""
                if changes and isinstance(changes[0], dict):
                    path_arg = str(changes[0].get("path", ""))
                params = {"command": str(item.get("command", ""))[:200]}
                if path_arg:
                    params["path"] = path_arg
                events.append(ActionEvent(turn, phase, tool,
                                          self._surface_for_path(path_arg), params, ""))
        return events

    def read_trace(self, root: Path, episode: int) -> Sequence[ActionEvent]:
        root = Path(root)
        events: list[ActionEvent] = []
        offset = 0
        for phase in PHASES:
            phase_events = self._read_native_trace(
                self._trace_path(root, episode, phase), phase)
            for event in phase_events:
                events.append(ActionEvent(event.turn + offset, event.phase, event.tool,
                                          event.surface, event.params, event.text))
            offset += max((event.turn for event in phase_events), default=0)
        return events
