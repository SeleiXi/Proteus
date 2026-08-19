"""Regressions for the four instrument defects found in the external review.

Each test is the reviewer's reproduction, kept as the thing that must not come back:
snapshots that miss ignored files, unit identities that collide, a fidelity ratio that
inverts, and a sweep that silently continues a previous sweep's evolved harness.
"""

import subprocess
from pathlib import Path

from proteus.core import snapshot
from proteus.core.adapter import Surface
from proteus.measure import crystallize, distance

# --------------------------------------------------------------- snapshots vs .gitignore

def _seed_harness(tmp_path: Path) -> Path:
    h = tmp_path / "harness"
    h.mkdir()
    (h / "notes.md").write_text("seed\n")
    return h


def test_ignored_files_are_snapshotted(tmp_path):
    h = _seed_harness(tmp_path)
    (h / ".gitignore").write_text("*.jsonl\nsecret/\n")   # written by the agent itself
    snapshot.init(h)
    (h / "log.jsonl").write_text('{"a":1}\n')
    (h / "secret").mkdir()
    (h / "secret" / "note.md").write_text("hidden\n")
    sha = snapshot.commit(h, "episode 1: x")
    listed = subprocess.run(
        ["git", "--git-dir", str(tmp_path / ".snapshot.git"), "ls-tree", "-r",
         "--name-only", sha],
        capture_output=True, text=True, check=True).stdout.split()
    assert "log.jsonl" in listed, "an ignored file is invisible to the instrument"
    assert "secret/note.md" in listed


def test_rejection_removes_ignored_files_too(tmp_path):
    h = _seed_harness(tmp_path)
    (h / ".gitignore").write_text("*.jsonl\n")
    snapshot.init(h)
    accepted = snapshot.head(h)
    (h / "notes.md").write_text("candidate\n")
    (h / "candidate.jsonl").write_text("junk\n")
    snapshot.commit(h, "candidate 1: x [rejected]")
    snapshot.restore(h, accepted)
    assert (h / "notes.md").read_text() == "seed\n"
    assert not (h / "candidate.jsonl").exists(), \
        "a rejected episode's ignored state survived the restore"


# ------------------------------------------------------------------- unit identity

def test_directory_units_see_every_member(tmp_path):
    h = tmp_path / "h"
    (h / "skills" / "alpha").mkdir(parents=True)
    (h / "skills" / "alpha" / "SKILL.md").write_text("how to alpha\n")
    (h / "skills" / "alpha" / "run.py").write_text("A = 1\n")
    surfaces = [Surface("skills", "skills", unit="directory")]
    before = distance.units(h, surfaces)
    (h / "skills" / "alpha" / "SKILL.md").write_text("how to alpha, revised\n")
    after = distance.units(h, surfaces)
    assert set(before["skills"]) == {"alpha"}
    assert before != after, "editing one file of a two-file skill was invisible"
    assert distance.compare(h, h, surfaces)["skills"].distance == 0.0


def test_file_units_do_not_collide_on_stem(tmp_path):
    h = tmp_path / "h"
    (h / "mem" / "a").mkdir(parents=True)
    (h / "mem" / "b").mkdir(parents=True)
    (h / "mem" / "a" / "note.md").write_text("one\n")
    (h / "mem" / "b" / "note.md").write_text("two\n")
    u = distance.units(h, [Surface("memory", "mem", unit="file")])["memory"]
    assert len(u) == 2, f"two files collapsed into {list(u)}"


def test_top_level_def_counts_defs_not_files(tmp_path):
    h = tmp_path / "h"
    h.mkdir()
    (h / "loop.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    surfaces = [Surface("loop", "loop.py", unit="top_level_def", is_code=True)]
    u = distance.units(h, surfaces)["loop"]
    assert set(u) == {"loop.py::a", "loop.py::b"}
    (h / "loop.py").write_text("def a():\n    return 99\n\n\ndef b():\n    return 2\n")
    v = distance.units(h, surfaces)["loop"]
    assert v["loop.py::b"] == u["loop.py::b"] and v["loop.py::a"] != u["loop.py::a"]


def test_unparseable_code_surface_still_measures(tmp_path):
    h = tmp_path / "h"
    h.mkdir()
    (h / "loop.py").write_text("def a(:\n")          # the agent broke its own loop
    u = distance.units(h, [Surface("loop", "loop.py", unit="top_level_def")])["loop"]
    assert u, "a syntactically broken harness file must still be measured"


# ------------------------------------------------------------------- fidelity ratio

def test_fidelity_is_worst_when_the_probe_matches_another_seed():
    own = ["read", "read", "write"]
    other = ["bash", "edit", "bash"]
    r = crystallize.fidelity(other, own, [other])   # probe == another endpoint exactly
    assert r["to_others"] == 0.0
    assert r["ratio"] > 10.0, "matching a foreign endpoint read as perfect fidelity"
    faithful = crystallize.fidelity(own, own, [other])
    assert faithful["ratio"] < 1.0


# ------------------------------------------------------------------- sweep contamination

def _sweep_cfg(root: Path, **kw):
    from proteus.adapters.minimal import MinimalHarness
    from proteus.core import NEUTRAL, GoalConfig
    from proteus.sweep import SweepConfig
    return SweepConfig(name="t", adapter_factory=MinimalHarness, arms=[NEUTRAL], seeds=1,
                       goal=GoalConfig(), root=root, model="mock", episodes=1, **kw)


def test_second_sweep_into_the_same_root_refuses(tmp_path):
    from proteus.sweep import run_sweep
    root = tmp_path / "out"
    run_sweep(_sweep_cfg(root))
    try:
        run_sweep(_sweep_cfg(root))
    except FileExistsError as exc:
        assert "on_existing" in str(exc)
    else:
        raise AssertionError("a second sweep silently reused the evolved run root")


def test_resume_skips_completed_seeds(tmp_path):
    from proteus.sweep import run_sweep
    root = tmp_path / "out"
    first = run_sweep(_sweep_cfg(root))
    again = run_sweep(_sweep_cfg(root, on_existing="resume"))
    assert first and again == [], "resume re-ran a finished seed"


# ------------------------------------------------------------------- user-set limits

def test_max_turns_actually_stops_the_episode(tmp_path):
    from proteus.adapters.minimal import MinimalHarness
    from proteus.core import NEUTRAL, GoalConfig
    from proteus.core.episode import RunConfig, run
    from proteus.measure import stream
    h = MinimalHarness()
    res = run(RunConfig(name="t", adapter=h, disposition=NEUTRAL, goal=GoalConfig(),
                        root=tmp_path / "r", model="mock", episodes=2, max_turns=3, seed=1))
    assert res.episodes_complete == 2
    for ep in (1, 2):
        assert len(stream.tool_stream(h.read_trace(tmp_path / "r", ep))) <= 3


def test_sandbox_config_from_an_image_reference():
    from proteus.sandbox import SandboxConfig
    c = SandboxConfig.from_spec("ghcr.io/someone/my-env:1.4", network="host", cpus="2")
    assert c.image == "ghcr.io/someone/my-env:1.4"
    assert c.network == "host" and c.cpus == "2"


def _has_toml() -> bool:
    try:
        import tomllib  # noqa: F401
    except ImportError:
        try:
            import tomli  # noqa: F401
        except ImportError:
            return False
    return True


def test_sandbox_config_from_a_manifest_directory(tmp_path):
    from proteus.sandbox import SandboxConfig
    if not _has_toml():
        return   # no TOML reader on this interpreter; the image-reference path covers the rest
    (tmp_path / "environment.toml").write_text(
        '[environment]\n'
        'docker_image = "me/env:9"\n'
        'network = "bridge"\n'
        'memory = "4g"\n'
        'workdir = "/work"\n'
        'docker_args = ["--gpus", "all"]\n'
        'env_passthrough = ["MY_KEY"]\n'
        '[environment.env]\n'
        'LANG = "C.UTF-8"\n', encoding="utf-8")
    c = SandboxConfig.from_spec(str(tmp_path))
    assert c.image == "me/env:9" and c.network == "bridge" and c.mem_limit == "4g"
    assert c.workdir == "/work" and c.extra_args == ("--gpus", "all")
    assert c.env == {"LANG": "C.UTF-8"} and c.env_passthrough == ("MY_KEY",)
    # a flag beats the manifest
    assert SandboxConfig.from_spec(str(tmp_path), network="none").network == "none"


def test_user_environment_reaches_the_docker_command(tmp_path):
    from proteus.sandbox import DockerSandbox, SandboxConfig
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        import subprocess as sp
        return sp.CompletedProcess(argv, 0, "", "")

    import proteus.sandbox.docker as mod
    real, mod.subprocess.run = mod.subprocess.run, fake_run
    try:
        DockerSandbox(SandboxConfig(
            image="me/env:9", network="bridge", mem_limit="4g", cpus="2",
            workdir="/work", user="1000:1000", extra_args=("--gpus", "all"),
            extra_mounts=(("/host/data", "/data"),), env={"LANG": "C.UTF-8"},
            env_passthrough=("MY_KEY",),
        )).run(tmp_path, ["echo", "hi"], {"MY_KEY": "v"}, timeout_s=5)
    finally:
        mod.subprocess.run = real
    argv = seen["argv"]
    for want in (["--network", "bridge"], ["--memory", "4g"], ["--cpus", "2"],
                 ["--workdir", "/work"], ["--user", "1000:1000"],
                 ["-v", "/host/data:/data"], ["-e", "MY_KEY=v"], ["-e", "LANG=C.UTF-8"]):
        assert any(argv[i:i + 2] == want for i in range(len(argv))), f"missing {want}"
    assert argv.index("--gpus") < argv.index("me/env:9"), "flags must precede the image"
    assert argv[argv.index("me/env:9") + 1:] == ["echo", "hi"]
