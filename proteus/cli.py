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
    if ":" in name:
        # your own adapter, no registration needed: --harness mypkg.mymodule:MyHarness
        import importlib
        mod_name, _, cls_name = name.partition(":")
        try:
            cls = getattr(importlib.import_module(mod_name), cls_name)
        except (ImportError, AttributeError) as exc:
            raise SystemExit(f"cannot load harness {name!r}: {exc}") from exc
        return cls
    raise SystemExit(f"unknown harness {name!r} "
                     "(built-in: minimal, llm, dsh, aki; or use <module>:<Class>)")


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


def _travel(run_root: Path, episodes: int, surfaces) -> dict:
    """Materialise every episode state from the snapshot chain and sum path length."""
    import tempfile
    from proteus.core import snapshot
    from proteus.measure import distance
    work = run_root / "harness"
    states = []
    with tempfile.TemporaryDirectory() as tmp:
        for ep in range(0, episodes + 1):
            sha = snapshot.commit_for_episode(work, ep)
            if sha is None:
                continue
            dest = Path(tmp) / f"s{ep}"
            snapshot.materialize(work, sha, dest)
            states.append(dest)
        return distance.path_length(states, surfaces)


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
    arm_travel = defaultdict(lambda: defaultdict(list))
    for rec in records:
        work = Path(rec["root"]) / "harness"
        final = distance.units(work, surfaces)
        for sname, u in final.items():
            arm_surface[rec["arm"]][sname].append(len(u))
        last = adapter.read_trace(Path(rec["root"]), rec["episodes_complete"])
        arm_streams[rec["arm"]].append(stream.tool_stream(last))
        if args.travel:
            pl = _travel(Path(rec["root"]), rec["episodes_complete"], surfaces)
            for sname, d in pl.items():
                arm_travel[rec["arm"]][sname].append(d.added + d.dropped + d.revised)

    names = [s.name for s in surfaces]
    print(f"{'arm':<16}{'seeds':>6}" + "".join(f"{n:>12}" for n in names) + "   (mean units built)")
    for arm in arm_surface:
        row = "".join(f"{st.mean(arm_surface[arm][n]):>12.1f}" for n in names)
        print(f"{arm:<16}{len(arm_streams[arm]):>6}{row}")

    if args.travel and arm_travel:
        print(f"\n{'arm':<16}{'':>6}" + "".join(f"{n:>12}" for n in names)
              + "   (mean travel: units added+dropped+revised along the path)")
        for arm in arm_travel:
            row = "".join(f"{st.mean(arm_travel[arm][n]):>12.1f}" for n in names)
            print(f"{arm:<16}{'':>6}{row}")

    if len(arm_streams) > 1:
        if any(len(v) >= 2 for v in arm_streams.values()):
            r = stream.between_within(dict(arm_streams), level="freq", permutations=2000)
            print(f"\nbehavioural R (between/within arms, last episode): "
                  f"{r['R']:.3f}  p={r['p']:.4f}")
        else:
            print("\nbehavioural R: not computed (needs 2+ seeds per arm)")
    return 0


def cmd_check(args) -> int:
    from proteus.testing import check_adapter
    adapter = _adapter_factory(args.harness)()
    return len(check_adapter(adapter, episode=args.episode))


def cmd_env_scaffold(args) -> int:
    from proteus.envs import scaffold
    manifest = scaffold(args.source, args.name, ref=args.ref,
                        use_local_dockerfile=args.local_dockerfile)
    print(f"scaffolded {manifest}")
    print(f"next: proteus env build {args.name}")
    return 0


def cmd_env_build(args) -> int:
    from proteus.envs import build
    tag = build(args.name)
    print(f"built {tag} (recorded in the manifest)")
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
    m.add_argument("--travel", action="store_true",
                   help="also compute per-surface path length over episode snapshots")
    m.set_defaults(func=cmd_measure)

    c = sub.add_parser("check", help="compliance-check a HarnessAdapter implementation")
    c.add_argument("--harness", required=True, help="built-in name or <module>:<Class>")
    c.add_argument("--episode", action="store_true",
                   help="also run one neutral episode (may launch containers / cost money)")
    c.set_defaults(func=cmd_check)

    e = sub.add_parser("env", help="prepared environments: scaffold/build from a harness repo")
    esub = e.add_subparsers(dest="env_cmd", required=True)
    es = esub.add_parser("scaffold", help="write environments/<name>/ for a harness repo")
    es.add_argument("--from", dest="source", required=True,
                    help="git URL or local path of the harness repo")
    es.add_argument("--name", required=True)
    es.add_argument("--ref", default="", help="branch/tag/sha to pin")
    es.add_argument("--local-dockerfile", action="store_true",
                    help="use a wrapper Dockerfile in environments/<name>/ (stub written)")
    es.set_defaults(func=cmd_env_scaffold)
    eb = esub.add_parser("build", help="build the environment image from its manifest")
    eb.add_argument("name", help="environment name or a path to environment.toml")
    eb.set_defaults(func=cmd_env_build)

    args = ap.parse_args(argv)
    if args.cmd == "run" and not args.arm:
        args.arm = ["neutral", "review:notes", "review:tools"]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
