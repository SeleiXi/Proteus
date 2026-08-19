"""The self-code arrangement for containerized harnesses, offline.

Both dsh and pi run source mode: seed() unpacks the image's source tar, the image's boot
wrapper rebuilds from /workspace/src, and check_boot() is the viability gate. The live
proofs (real extraction, a marker edit surviving the rebuild, a planted error refused)
need Docker and run in the release smoke; these cover the adapter logic with a fake
sandbox.
"""

import subprocess
from pathlib import Path

from proteus.adapters.dsh import DshHarness
from proteus.adapters.pi import PiHarness


class FakeSandbox:
    def __init__(self, boot_rc=0):
        self.boot_rc = boot_rc
        self.calls = []

    def run(self, run_root, command, env, timeout_s, mounts=(), stop_check=None):
        self.calls.append({"command": command, "mounts": mounts,
                           "stop_check": stop_check})
        if command != ["--version"] and stop_check is not None and stop_check():
            return subprocess.CompletedProcess(command, 137, "", "killed")
        rc = self.boot_rc if command == ["--version"] else 0
        return subprocess.CompletedProcess(command, rc, "ok", "boom" if rc else "")


def _seed_with_fake_src(adapter, harness: Path, pieces):
    adapter._extract_self_code = lambda dest: None      # no docker in offline tests
    adapter.seed(harness, 0)
    for piece in pieces:
        p = harness / "src" / piece
        if "." in piece:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}")
        else:
            p.mkdir(parents=True, exist_ok=True)
            (p / "code.js").write_text("// self\n")


def test_loop_surface_is_declared_and_mapped():
    for cls in (DshHarness, PiHarness):
        a = cls(key="x", sandbox=FakeSandbox())
        names = {s.name: s for s in a.surfaces()}
        assert "loop" in names and names["loop"].is_code
        assert a._surface_for_path("/workspace/src/lib/bin.js") == "loop"


def test_source_mode_gates_through_the_boot_contract(tmp_path):
    # the boot wrapper rebuilds from /workspace/src, so the gate needs no extra mounts:
    # workspace + state are the whole contract, for both containerized harnesses
    from proteus.core.adapter import EpisodeSpec
    for i, cls in enumerate((DshHarness, PiHarness)):
        sandbox = FakeSandbox()
        a = cls(key="x", sandbox=sandbox)
        h = tmp_path / f"harness{i}"
        _seed_with_fake_src(a, h, ("packages",))
        root = h.parent / f"root{i}"
        root.mkdir(exist_ok=True)
        (root / "harness").symlink_to(h)
        a.run_episode(EpisodeSpec(root=root, episode=1, model="m", phase_prompts={}))
        gate = sandbox.calls[0]
        assert gate["command"] == ["--version"], cls.__name__
        conts = {cont for _, cont in gate["mounts"]}
        assert conts == {"/workspace", "/state"},             f"{cls.__name__}: the gate must run exactly the boot contract"


def test_broken_self_code_fails_the_episode_legibly(tmp_path):
    from proteus.core.adapter import EpisodeSpec
    for i, cls in enumerate((DshHarness, PiHarness)):
        sandbox = FakeSandbox(boot_rc=97)
        a = cls(key="x", sandbox=sandbox)
        h = tmp_path / f"harness{i}"
        _seed_with_fake_src(a, h, ("packages",))
        root = h.parent / f"root{i}"
        root.mkdir(exist_ok=True)
        (root / "harness").symlink_to(h)
        res = a.run_episode(EpisodeSpec(root=root, episode=1, model="m", phase_prompts={}))
        assert not res.ok and "does not boot" in res.error, cls.__name__
        # the gate ran once and no phase was attempted after it
        assert [c["command"] for c in sandbox.calls] == [["--version"]], cls.__name__


def test_reseeding_never_overwrites_evolved_code(tmp_path):
    a = DshHarness(key="x", sandbox=FakeSandbox())
    h = tmp_path / "harness"
    _seed_with_fake_src(a, h, ("lib",))
    (h / "src" / "lib" / "code.js").write_text("// evolved by the agent\n")
    calls = []
    a._extract_self_code = lambda dest: calls.append(dest)

    real = DshHarness(key="x", sandbox=FakeSandbox())
    # the real guard lives in _extract_self_code itself: non-empty src is left alone
    real._extract_self_code(h / "src")
    assert (h / "src" / "lib" / "code.js").read_text() == "// evolved by the agent\n"


# ------------------------------------------------------------------- turn budget

def test_budget_stops_new_phases_exactly(tmp_path):
    # the between-phase check is exact and needs nothing from the log format
    from proteus.core.adapter import EpisodeSpec
    for i, cls in enumerate((DshHarness, PiHarness)):
        sandbox = FakeSandbox()
        a = cls(key="x", sandbox=sandbox)
        a._live_calls = lambda *args, **kw: 99          # already over budget
        h = tmp_path / f"h{i}"
        _seed_with_fake_src(a, h, ())
        root = tmp_path / f"r{i}"
        root.mkdir()
        (root / "harness").symlink_to(h)
        res = a.run_episode(EpisodeSpec(root=root, episode=1, model="m",
                                        phase_prompts={}, max_turns=10))
        assert res.ok and not res.error, cls.__name__
        assert res.counters["turn_capped"], cls.__name__
        phases = [c for c in sandbox.calls if c["command"] != ["--version"]]
        assert phases == [], f"{cls.__name__}: a phase ran past the budget"


def test_budget_kill_mid_phase_is_a_cap_not_an_error(tmp_path):
    from proteus.core.adapter import EpisodeSpec
    for i, cls in enumerate((DshHarness, PiHarness)):
        sandbox = FakeSandbox()
        a = cls(key="x", sandbox=sandbox)
        calls = iter([0, 50])                            # under budget at launch, over mid-phase
        a._live_calls = lambda *args, **kw: next(calls, 50)
        h = tmp_path / f"h{i}"
        _seed_with_fake_src(a, h, ())
        root = tmp_path / f"r{i}"
        root.mkdir()
        (root / "harness").symlink_to(h)
        res = a.run_episode(EpisodeSpec(root=root, episode=1, model="m",
                                        phase_prompts={}, max_turns=10))
        assert res.ok and not res.error, \
            f"{cls.__name__}: a budget kill must be a cap, got error={res.error!r}"
        assert res.counters["turn_capped"], cls.__name__
        phases = [c for c in sandbox.calls if c["command"] != ["--version"]]
        assert len(phases) == 1, f"{cls.__name__}: phases continued after the kill"


def test_no_budget_means_no_watching(tmp_path):
    from proteus.core.adapter import EpisodeSpec
    sandbox = FakeSandbox()
    a = PiHarness(key="x", sandbox=sandbox)
    h = tmp_path / "h"
    _seed_with_fake_src(a, h, ())
    root = tmp_path / "r"
    root.mkdir()
    (root / "harness").symlink_to(h)
    a.run_episode(EpisodeSpec(root=root, episode=1, model="m", phase_prompts={},
                              max_turns=0))
    phases = [c for c in sandbox.calls if c["command"] != ["--version"]]
    assert phases and all(c["stop_check"] is None for c in phases)


def test_announced_budget_reaches_every_phase_prompt(tmp_path):
    from proteus.adapters.minimal import MinimalHarness
    from proteus.core import NEUTRAL, GoalConfig
    from proteus.core.episode import PHASES, RunConfig, _phase_prompts
    on = RunConfig(name="t", adapter=MinimalHarness(), disposition=NEUTRAL,
                   goal=GoalConfig(), root=tmp_path, model="mock", max_turns=12,
                   announce_budget=True)
    p = _phase_prompts(on, "")
    assert all("at most 12 tool calls" in p[ph] for ph in PHASES)
    off = RunConfig(name="t", adapter=MinimalHarness(), disposition=NEUTRAL,
                    goal=GoalConfig(), root=tmp_path, model="mock", max_turns=12)
    q = _phase_prompts(off, "")
    assert all("tool calls in this episode" not in q[ph] for ph in PHASES)
