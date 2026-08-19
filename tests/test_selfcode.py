"""The self-code arrangement for containerized harnesses, offline.

The live proofs (extraction, copy-boot via a version edit, broken-loop gate) need Docker
and run in the release smoke; these cover the adapter logic with a fake sandbox.
"""

import subprocess
from pathlib import Path

from proteus.adapters.dsh import DshHarness
from proteus.adapters.pi import PiHarness


class FakeSandbox:
    def __init__(self, boot_rc=0):
        self.boot_rc = boot_rc
        self.calls = []

    def run(self, run_root, command, env, timeout_s, mounts=()):
        self.calls.append({"command": command, "mounts": mounts})
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


def test_shadow_mounts_cover_only_what_exists(tmp_path):
    a = DshHarness(key="x", sandbox=FakeSandbox())
    h = tmp_path / "harness"
    _seed_with_fake_src(a, h, ("lib", "package.json"))   # no config/
    mounts = a._shadow_mounts(h)
    assert len(mounts) == 2
    for host, cont in mounts:
        assert host.startswith(str(h / "src"))
        assert cont.startswith(a.pkg_path)
    assert not any(cont.endswith("/config") for _, cont in mounts)


def test_broken_self_code_fails_the_episode_legibly(tmp_path):
    from proteus.core.adapter import EpisodeSpec
    sandbox = FakeSandbox(boot_rc=2)
    a = DshHarness(key="x", sandbox=sandbox)
    h = tmp_path / "harness"
    _seed_with_fake_src(a, h, ("lib",))
    res = a.run_episode(EpisodeSpec(root=tmp_path, episode=1, model="m", phase_prompts={}))
    assert not res.ok
    assert "does not boot" in res.error
    # the gate ran once and no phase was attempted after it
    assert [c["command"] for c in sandbox.calls] == [["--version"]]


def test_phases_carry_the_shadow_mounts(tmp_path):
    from proteus.core.adapter import EpisodeSpec
    sandbox = FakeSandbox()
    a = PiHarness(key="x", sandbox=sandbox)
    h = tmp_path / "harness"
    _seed_with_fake_src(a, h, ("dist", "package.json"))
    a.run_episode(EpisodeSpec(root=tmp_path, episode=1, model="m", phase_prompts={}))
    phase_calls = [c for c in sandbox.calls if c["command"] != ["--version"]]
    assert phase_calls, "no phases ran"
    for call in phase_calls:
        conts = [cont for _, cont in call["mounts"]]
        assert f"{a.pkg_path}/dist" in conts, "a phase booted without the seed's own code"


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
