"""Run the public DeepSeek Harness audio-modality evolution experiment.

Build the pinned rc.8 source image first (docs/DSH_AUDIO_EVOLUTION.md), export a DeepSeek
API key, then run:

    python examples/dsh_audio_evolution.py --out runs/dsh-audio-30ep-phase-budget-v2

The equivalent CLI is printed by ``--dry-run``.  The Python entry point exists so the
campaign's exact goal and benchmark stay versioned rather than copied from a shell history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from functools import partial
from pathlib import Path

from proteus import __version__
from proteus.adapters.dsh import DshHarness
from proteus.bench.dsh_audio import GOAL_TEXT, NAME, evaluate_audio_capability
from proteus.core import EvaluatorSpec, GoalConfig, NEUTRAL, Visibility
from proteus.core.continuity import HandoffStore
from proteus.report import write_report
from proteus.sweep import SweepConfig, run_sweep


class SnapshotSeededDshHarness(DshHarness):
    """Start a new, condition-locked trajectory from an earlier valid harness snapshot."""

    def __init__(self, initial_harness: Path, initial_handoff: Path | None = None,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self.initial_harness = Path(initial_harness).expanduser().resolve()
        self.initial_handoff = (
            Path(initial_handoff).expanduser().resolve()
            if initial_handoff is not None else None
        )

    def seed(self, harness_root: Path, rng_seed: int = 0) -> None:
        del rng_seed
        source = self.initial_harness
        if not (source / "src").is_dir() or not (source / "AGENTS.md").is_file():
            raise ValueError(
                f"continuation seed is not a DSH harness snapshot: {source}"
            )
        if harness_root.exists():
            raise FileExistsError(
                f"continuation destination already exists: {harness_root}"
            )
        harness_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, harness_root, symlinks=True)
        if self.initial_handoff is not None:
            if not self.initial_handoff.is_file():
                raise ValueError(
                    f"continuation handoff does not exist: {self.initial_handoff}"
                )
            content = self.initial_handoff.read_text(encoding="utf-8")
            handoffs = HandoffStore(harness_root.parent)
            handoffs.initialise()
            handoffs.latest.write_text(content.rstrip() + "\n", encoding="utf-8")
            handoffs.current.write_text(content.rstrip() + "\n", encoding="utf-8")


def _write_live_state(root: Path, status: str) -> None:
    payload = {
        "schema": 1,
        "status": status,
        "heartbeat_at": time.time(),
        "proteus_version": __version__,
    }
    path = root / "live-state.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _heartbeat(root: Path, stopped: threading.Event) -> None:
    while not stopped.wait(10):
        _write_live_state(root, "running")


def _start_publisher(args: argparse.Namespace, root: Path) -> subprocess.Popen:
    repository_root = Path(__file__).resolve().parents[1]
    output = (
        Path(args.live_feed).expanduser().resolve()
        if args.live_feed else root / "public-feed.json"
    )
    command = [
        sys.executable,
        str(repository_root / "scripts" / "dsh_audio_live.py"),
        "--sweep", str(root),
        "--out", str(output),
        "--watch", str(args.live_watch),
        "--repo", args.live_repo,
        "--remote-path", args.live_remote_path,
        "--token-env", args.live_token_env,
    ]
    print("Public trace: https://proteus-evolve.github.io/playground.html#live-campaign",
          flush=True)
    return subprocess.Popen(command)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="runs/dsh-audio-30ep-phase-budget-v2")
    parser.add_argument("--episodes", type=int, default=30)
    parser.add_argument("--max-turns", type=int, default=300)
    parser.add_argument("--hard-max-turns", type=int, default=500)
    parser.add_argument("--observe-turns", type=int, default=40)
    parser.add_argument("--propose-turns", type=int, default=25)
    parser.add_argument("--act-turns", type=int, default=200)
    parser.add_argument("--reflect-turns", type=int, default=35)
    parser.add_argument("--checkpoint-turns", type=int, default=2)
    parser.add_argument(
        "--phase-timeout", type=int, default=7200, metavar="SECONDS",
        help="wall-clock safety limit for one DSH phase (default: 7200)",
    )
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--image", default="proteus-env-dsh-src:0.1.0-rc.8")
    parser.add_argument(
        "--dsh-permission-mode",
        choices=("workspace-write", "danger-full-access"),
        default="workspace-write",
        help=("DSH's inner file policy. Use danger-full-access only when Proteus is "
              "already providing the outer container boundary and the native DSH "
              "sandbox is unavailable on the image architecture."),
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--live", action="store_true",
                        help="publish each completed episode to the public Evolving Lab")
    parser.add_argument("--live-repo", default="proteus-evolve/proteus-evolve.github.io")
    parser.add_argument("--live-remote-path", default="assets/dsh-audio-live.json")
    parser.add_argument("--live-token-env", default="GH_TOKEN")
    parser.add_argument("--live-feed", help="optional local copy of the public JSON feed")
    parser.add_argument("--live-watch", type=float, default=15)
    parser.add_argument(
        "--initial-harness", type=Path,
        help=("start a new trajectory from this valid harness snapshot instead of the "
              "pinned rc.8 image seed"),
    )
    parser.add_argument(
        "--initial-handoff", type=Path,
        help="bounded operational handoff carried from the parent trajectory",
    )
    parser.add_argument(
        "--parent-snapshot", default="",
        help="non-secret snapshot identifier recorded as continuation lineage",
    )
    parser.add_argument(
        "--episode-offset", type=int, default=0,
        help="logical episode count completed by the parent trajectory",
    )
    args = parser.parse_args()
    if args.phase_timeout <= 0:
        parser.error("--phase-timeout must be positive; use a large value to approximate no timeout")
    phase_turns = {
        "observe": args.observe_turns,
        "propose": args.propose_turns,
        "act": args.act_turns,
        "reflect": args.reflect_turns,
    }
    if any(turns <= 0 for turns in phase_turns.values()):
        parser.error("every phase budget must be positive")
    if sum(phase_turns.values()) != args.max_turns:
        parser.error("phase budgets must sum to --max-turns")
    if args.hard_max_turns < args.max_turns:
        parser.error("--hard-max-turns must be at least --max-turns")
    if args.episode_offset < 0:
        parser.error("--episode-offset cannot be negative")
    if args.initial_harness is None and (
        args.initial_handoff is not None or args.parent_snapshot or args.episode_offset
    ):
        parser.error(
            "--initial-handoff/--parent-snapshot/--episode-offset require --initial-harness"
        )
    if args.initial_harness is not None and not args.parent_snapshot:
        parser.error("--initial-harness requires --parent-snapshot for durable lineage")

    benchmark = EvaluatorSpec(
        name=NAME,
        run=evaluate_audio_capability,
        kind="benchmark",
        visibility=Visibility.OBSERVE,
    )
    root = Path(args.out).expanduser().resolve()
    adapter_kwargs = {
        "image": args.image,
        "permission_mode": args.dsh_permission_mode,
        "phase_timeout_s": args.phase_timeout,
    }
    if args.initial_harness is None:
        adapter_factory = partial(DshHarness, **adapter_kwargs)
        lineage = {}
    else:
        initial_harness = args.initial_harness.expanduser().resolve()
        initial_handoff = (
            args.initial_handoff.expanduser().resolve()
            if args.initial_handoff is not None else None
        )
        adapter_factory = partial(
            SnapshotSeededDshHarness,
            initial_harness=initial_harness,
            initial_handoff=initial_handoff,
            **adapter_kwargs,
        )
        handoff_sha256 = (
            hashlib.sha256(initial_handoff.read_bytes()).hexdigest()
            if initial_handoff is not None and initial_handoff.is_file() else ""
        )
        lineage = {
            "continuation": {
                "parent_snapshot": args.parent_snapshot,
                "logical_episode_offset": args.episode_offset,
                "parent_handoff_sha256": handoff_sha256,
            }
        }
    cfg = SweepConfig(
        name="dsh-rc8-audio-modality-phase-budget-v2",
        adapter_factory=adapter_factory,
        arms=(NEUTRAL,),
        seeds=1,
        goal=GoalConfig.of(text=GOAL_TEXT, evaluators=(benchmark,)),
        root=root,
        model=args.model,
        episodes=args.episodes,
        max_turns=args.max_turns,
        phase_turns=phase_turns,
        hard_max_turns=args.hard_max_turns,
        checkpoint_turns=args.checkpoint_turns,
        announce_budget=True,
        on_existing="resume" if args.resume else "refuse",
        condition_metadata=lineage,
    )
    root.mkdir(parents=True, exist_ok=True)
    write_report(root)
    print(f"Live local report: {root / 'report.html'}", flush=True)
    publisher = None
    heartbeat = None
    stopped = threading.Event()
    final_status = "paused"
    exit_code = 0
    if args.live:
        _write_live_state(root, "starting")
        heartbeat = threading.Thread(target=_heartbeat, args=(root, stopped), daemon=True)
        heartbeat.start()
        publisher = _start_publisher(args, root)
    try:
        records = run_sweep(cfg)
        if any(record.get("error") for record in records):
            final_status = "error"
        elif records and any(record.get("episodes_complete", 0) < args.episodes for record in records):
            final_status = "paused"
        else:
            final_status = "complete"
        write_report(root)
    except KeyboardInterrupt:
        final_status = "paused"
        exit_code = 130
        resume_flags = "--resume --live" if args.live else "--resume"
        print(f"Evolution paused; rerun with {resume_flags} to continue.", flush=True)
    except Exception:
        final_status = "error"
        raise
    finally:
        if args.live:
            stopped.set()
            if heartbeat:
                heartbeat.join(timeout=2)
            _write_live_state(root, final_status)
            if publisher:
                try:
                    publisher.wait(timeout=max(args.live_watch * 2 + 15, 30))
                except subprocess.TimeoutExpired:
                    publisher.terminate()
                    publisher.wait(timeout=10)
                if publisher.returncode:
                    print("Warning: the public feed publisher stopped with an error; "
                          "the local run is preserved and can be republished.", file=sys.stderr)
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
