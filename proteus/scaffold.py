"""Scaffold a new adapter or benchmark from the committed templates.

    python -m proteus.scaffold adapter MyHarness           # -> proteus/adapters/myharness.py
    python -m proteus.scaffold benchmark my_task           # -> proteus/bench/my_task.py
    python -m proteus.scaffold adapter MyHarness --dest path/to/file.py

The single source of truth for each skeleton is `examples/adapter_template.py` /
`examples/benchmark_template.py` — this command copies one and renames the sentinel
identifiers so `proteus check` (adapter) or the grader (benchmark) works immediately.
Scaffolding is a source-checkout activity: the templates live under `examples/`, which is
not shipped in the wheel, so run this from a clone (an editable `pip install -e .` is
fine).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _repo_root() -> Path:
    # proteus/ is a direct child of the repo root in a source checkout / editable install.
    return Path(__file__).resolve().parent.parent


def _dest_module(dest: Path) -> str:
    """`proteus/adapters/foo.py` -> `proteus.adapters.foo`, relative to the repo root."""
    try:
        rel = dest.resolve().relative_to(_repo_root())
    except ValueError:
        return dest.stem  # dest is outside the repo; best we can offer is the module name
    return ".".join(rel.with_suffix("").parts)


def scaffold_adapter(name: str, dest: Path, *, force: bool = False) -> Path:
    """Write a new adapter skeleton named `<name>Harness` to `dest`."""
    cls = name if name.endswith("Harness") else f"{name}Harness"
    short = re.sub(r"Harness$", "", cls)
    short = re.sub(r"(?<!^)(?=[A-Z])", "_", short).lower()  # CamelCase -> snake_case
    template = _repo_root() / "examples" / "adapter_template.py"
    subs = {
        "TemplateHarness": cls,
        "examples.adapter_template": _dest_module(dest),
        'name = "template"': f'name = "{short}"',
    }
    return _render(template, dest, subs, force=force)


def scaffold_benchmark(name: str, dest: Path, *, force: bool = False) -> Path:
    """Write a new benchmark (`BenchTask`) skeleton identified by `name` to `dest`."""
    ident = re.sub(r"[^0-9a-zA-Z_-]", "-", name)
    template = _repo_root() / "examples" / "benchmark_template.py"
    subs = {
        "examples.benchmark_template": _dest_module(dest),
        '"template-add"': f'"{ident}"',
        "name=\"template-add\"": f'name="{ident}"',
    }
    return _render(template, dest, subs, force=force)


def _render(template: Path, dest: Path, subs: dict[str, str], *, force: bool) -> Path:
    if not template.exists():
        raise SystemExit(f"template not found: {template}\n"
                         "run scaffold from a source checkout (see `pip install -e .`).")
    if dest.exists() and not force:
        raise SystemExit(f"{dest} already exists (use --force to overwrite)")
    text = template.read_text(encoding="utf-8")
    for old, new in subs.items():
        text = text.replace(old, new)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="proteus.scaffold", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="kind", required=True)

    a = sub.add_parser("adapter", help="new HarnessAdapter from examples/adapter_template.py")
    a.add_argument("name", help="class name, e.g. MyHarness (the 'Harness' suffix is optional)")
    a.add_argument("--dest", default="", help="output path (default: proteus/adapters/<name>.py)")
    a.add_argument("--force", action="store_true", help="overwrite an existing file")

    b = sub.add_parser("benchmark", help="new BenchTask from examples/benchmark_template.py")
    b.add_argument("name", help="benchmark id, e.g. my_task")
    b.add_argument("--dest", default="", help="output path (default: proteus/bench/<name>.py)")
    b.add_argument("--force", action="store_true", help="overwrite an existing file")

    args = ap.parse_args(argv)

    if args.kind == "adapter":
        short = re.sub(r"Harness$", "", args.name)
        short = re.sub(r"(?<!^)(?=[A-Z])", "_", short).lower()
        dest = Path(args.dest) if args.dest else _repo_root() / "proteus" / "adapters" / f"{short}.py"
        out = scaffold_adapter(args.name, dest, force=args.force)
        mod = _dest_module(out)
        cls = args.name if args.name.endswith("Harness") else args.name + "Harness"
        print(f"scaffolded {out}")
        print(f"next: implement the TODOs, then  proteus check --harness {mod}:{cls} --episode")
    else:
        dest = Path(args.dest) if args.dest else _repo_root() / "proteus" / "bench" / f"{args.name}.py"
        out = scaffold_benchmark(args.name, dest, force=args.force)
        print(f"scaffolded {out}")
        print("next: implement setup()/grade(), then wire TASK into a run via as_goal(TASK)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
