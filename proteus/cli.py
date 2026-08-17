"""Proteus command line.

    proteus run     --harness minimal --arm review:notes --goal none \
                    --seeds 4 --episodes 10 --out runs/demo
    proteus measure --out runs/demo          # structural + behavioural distance, per arm

`--arm` is `neutral`, or `review:<surface>` / `record:<surface>` (repeatable). `--goal` is
`none` (no-goal) or `task:<text>` (a stated objective). The default harness is `minimal`,
which runs offline; `aki` plugs in the reference research harness.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from proteus.core.disposition import NEUTRAL, record, review
from proteus.core.goal import Goal, GoalConfig
from proteus.sweep import SweepConfig, run_sweep


def _adapter_factory(name: str):
    if name == "minimal":
        from proteus.adapters.minimal import MinimalHarness
        return MinimalHarness
    if name == "llm":
        from proteus.adapters.llm import LLMHarness
        return LLMHarness
    if name == "dsh":
        from proteus.adapters.dsh import DshHarness
        return DshHarness
    if name == "aki":
        from proteus.adapters.aki import AkiHarness
        return AkiHarness
    raise SystemExit(f"unknown harness {name!r} (have: minimal, llm, dsh, aki)")


def _arm(spec: str):
    if spec == "neutral":
        return NEUTRAL
    kind, _, surface = spec.partition(":")
    if kind == "review" and surface:
        return review(surface)
    if kind == "record" and surface:
        return record(surface)
    raise SystemExit(f"bad --arm {spec!r} (use neutral | review:<surface> | record:<surface>)")


def _goal(spec: str) -> GoalConfig:
    if spec == "none":
        return GoalConfig.no_goal()
    kind, _, text = spec.partition(":")
    if kind == "task":
        return GoalConfig.single(Goal(name="task", text=text))
    raise SystemExit(f"bad --goal {spec!r} (use none | task:<text>)")


def cmd_run(args) -> int:
    cfg = SweepConfig(
        name=args.out,
        adapter_factory=_adapter_factory(args.harness),
        arms=[_arm(a) for a in args.arm],
        seeds=args.seeds,
        goal=_goal(args.goal),
        root=Path(args.out).expanduser(),
        model=args.model,
        episodes=args.episodes,
        max_turns=args.max_turns,
    )
    records = run_sweep(cfg)
    done = sum(r["episodes_complete"] for r in records)
    print(f"ran {len(records)} seeds, {done} episodes -> {args.out}")
    return 0


def cmd_measure(args) -> int:
    import statistics as st
    from collections import defaultdict
    from proteus.measure import distance, stream
    root = Path(args.out).expanduser()
    adapter = _adapter_factory(args.harness)()
    surfaces = adapter.surfaces()
    records = [json.loads(l) for l in (root / "seeds.jsonl").read_text().splitlines()]

    # structural: per-arm, per-surface final unit counts (what got built)
    arm_surface = defaultdict(lambda: defaultdict(list))
    arm_streams = defaultdict(list)
    for rec in records:
        work = Path(rec["root"]) / "harness"
        final = distance.units(work, surfaces)
        for sname, u in final.items():
            arm_surface[rec["arm"]][sname].append(len(u))
        last = adapter.read_trace(Path(rec["root"]), rec["episodes_complete"])
        arm_streams[rec["arm"]].append(stream.tool_stream(last))

    names = [s.name for s in surfaces]
    print(f"{'arm':<16}{'seeds':>6}" + "".join(f"{n:>12}" for n in names) + "   (mean units built)")
    for arm in arm_surface:
        row = "".join(f"{st.mean(arm_surface[arm][n]):>12.1f}" for n in names)
        print(f"{arm:<16}{len(arm_streams[arm]):>6}{row}")

    if len(arm_streams) > 1:
        if any(len(v) >= 2 for v in arm_streams.values()):
            r = stream.between_within(dict(arm_streams), level="freq", permutations=2000)
            print(f"\nbehavioural R (between/within arms, last episode): "
                  f"{r['R']:.3f}  p={r['p']:.4f}")
        else:
            print("\nbehavioural R: not computed (needs 2+ seeds per arm)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="proteus", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a self-evolution sweep")
    r.add_argument("--harness", default="minimal")
    r.add_argument("--arm", action="append", default=None,
                   help="neutral | review:<surface> | record:<surface> (repeatable)")
    r.add_argument("--goal", default="none", help="none | task:<text>")
    r.add_argument("--seeds", type=int, default=4)
    r.add_argument("--episodes", type=int, default=10)
    r.add_argument("--max-turns", type=int, default=100)
    r.add_argument("--model", default="",
                   help="model name; empty uses the adapter's default")
    r.add_argument("--out", required=True)
    r.set_defaults(func=cmd_run)

    m = sub.add_parser("measure", help="measure a finished sweep")
    m.add_argument("--harness", default="minimal")
    m.add_argument("--out", required=True)
    m.set_defaults(func=cmd_measure)

    args = ap.parse_args(argv)
    if args.cmd == "run" and not args.arm:
        args.arm = ["neutral", "review:notes", "review:tools"]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
