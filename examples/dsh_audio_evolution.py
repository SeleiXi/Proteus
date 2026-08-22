"""Run the public DeepSeek Harness audio-modality evolution experiment.

Build the pinned rc.8 source image first (docs/DSH_AUDIO_EVOLUTION.md), export a DeepSeek
API key, then run:

    python examples/dsh_audio_evolution.py --out runs/dsh-audio-30ep-phase-budget-v2

The equivalent CLI is printed by ``--dry-run``.  The Python entry point exists so the
campaign's exact goal and benchmark stay versioned rather than copied from a shell history.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from proteus import __version__
from proteus.adapters.dsh import DshHarness
from proteus.bench.dsh_audio import GOAL_TEXT, NAME, evaluate_audio_capability
from proteus.core import EvaluatorSpec, GoalConfig, NEUTRAL, Visibility
from proteus.report import write_report
from proteus.sweep import SweepConfig, run_sweep


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

    benchmark = EvaluatorSpec(
        name=NAME,
        run=evaluate_audio_capability,
        kind="benchmark",
        visibility=Visibility.OBSERVE,
    )
    root = Path(args.out).expanduser().resolve()
    cfg = SweepConfig(
        name="dsh-rc8-audio-modality-phase-budget-v2",
        adapter_factory=lambda: DshHarness(
            image=args.image, permission_mode=args.dsh_permission_mode,
            phase_timeout_s=args.phase_timeout,
        ),
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
